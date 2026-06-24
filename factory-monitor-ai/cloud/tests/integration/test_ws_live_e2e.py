"""Phase 2b end-to-end: /ws/live carries the full incident lifecycle.

Real testcontainers Postgres (postgres:16) + Redis (redis:7-alpine).  A
Starlette TestClient drives a real WebSocket connection against the FastAPI
app.  DB writes go through the REAL service/worker code; incident-change events
travel the REAL Redis publish path (publish_incident_event → Redis channel →
RedisFanoutSubscriber → ConnectionManager.broadcast → WS client).

The subscriber runs in the TestClient's event loop (started via a startup
event handler on the app).  The test itself is async so it can call async
service functions; the WS client is sync (Starlette TestClient) because httpx
has no WebSocket client.  The two loops are bridged by Redis: the test
publishes compact change events from its own event loop; the subscriber running
in the TestClient loop receives and broadcasts them to the connected WS client.

Frame-draining uses concurrent.futures with a wall-clock deadline (matching the
pattern in test_ws_live_endpoint.py) so no fixed sleeps are needed.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.config import Settings
from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.incident_actions import acknowledge_incident as _ack_incident
from cloud.common.on_call_resolver import resolve as _resolve_on_call
from cloud.common.schemas.anomaly import AnomalyEvent, AnomalyType, Evidence, Severity
from cloud.common.ws.subscriber import RedisFanoutSubscriber
from cloud.common.ws_events import (
    CHANGE_CREATED,
    CHANGE_TIER_ADVANCED,
    CHANGE_UPDATED,
    incident_change,
)
from cloud.common.ws_publisher import publish_incident_event
from cloud.escalation_worker.worker import EscalationWorker
from cloud.ingest_worker.service import create_incident_from_anomaly

# Reuse the proven seed helper + _make_incident_due from the Phase-2a e2e.
from cloud.tests.integration.test_phase2a_e2e import (
    _make_incident_due,
    _seed_roster_and_tiers,
)

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")
_WS_CHANNEL = "dashboard:incidents:e2e"


def _async_url(sync_url: str) -> str:
    return (
        sync_url
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _make_anomaly_event(*, camera_id: str = "cam_e2e_07") -> AnomalyEvent:
    bucket = "2026062410"
    dedup_key = f"{camera_id}|{camera_id}:1|PPE_NO_HARDHAT|{bucket}"
    return AnomalyEvent(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type=AnomalyType.PPE_NO_HARDHAT,
        rule_id="PPE_NO_HARDHAT",
        occurred_at=datetime.now(UTC),
        site_id="plant-01",
        camera_id=camera_id,
        zone_id="zone_weld_bay",
        track_id=f"{camera_id}:1",
        object_class="person",
        severity=Severity.HIGH,
        confidence=0.91,
        dedup_key=dedup_key,
        evidence=Evidence(bbox=[100, 100, 50, 100], snapshot_url="", footage_source=""),
        source="edge",
    )


async def _resolver(session, role, site_id, zone_id, at):
    return await _resolve_on_call(session, role=role, site_id=site_id, zone_id=zone_id, at=at)


def _drain_until(ws, *types: str, timeout: float = 8.0) -> dict:
    """Drain WS frames until one whose ``type`` is in *types*; return it.

    Skips system.heartbeat / timer.snapshot re-anchor frames.  Uses a
    ThreadPoolExecutor to impose a wall-clock deadline on each blocking
    ws.receive_json() call so the test never hangs indefinitely.
    """
    wanted = set(types)
    deadline = timeout

    def _read_one() -> dict:
        return ws.receive_json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        remaining = deadline
        while remaining > 0:
            fut = pool.submit(_read_one)
            try:
                frame = fut.result(timeout=remaining)
            except concurrent.futures.TimeoutError as exc:
                raise AssertionError(
                    f"did not receive any of {wanted} within {timeout}s"
                ) from exc
            if frame.get("type") in wanted:
                return frame
            # Not our frame — keep draining; subtract a tiny floor.
            remaining -= 0.01
    raise AssertionError(f"did not receive any of {wanted} within {timeout}s")


@contextlib.contextmanager
def _ws_session(client: TestClient, path: str):
    """Suppress the CancelledError Starlette's TestClient can raise on WS teardown."""
    try:
        with client.websocket_connect(path) as ws:
            yield ws
    except concurrent.futures.CancelledError:
        pass  # benign teardown race — all assertions run inside the with block


# ── module-scoped containers ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def _redis_container():
    with RedisContainer("redis:7-alpine") as rc:
        yield rc


@pytest.fixture(scope="module")
def _pg_async_url(_pg_container: PostgresContainer) -> str:
    sync_url = _pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest.fixture(scope="module")
def _redis_url(_redis_container: RedisContainer) -> str:
    host = _redis_container.get_container_host_ip()
    port = _redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


# ── per-test fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def maker(_pg_async_url: str):
    """Async session-maker for the test's own event loop (service calls + seeds)."""
    engine = create_async_engine(_pg_async_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_roster_and_tiers(m)
    yield m
    await engine.dispose()


@pytest.fixture
async def pub_client(_redis_url: str):
    """A Redis client in the test's event loop for publishing compact change events."""
    client = aioredis.from_url(_redis_url, decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture
def app(_pg_async_url: str, _redis_url: str):
    """Build the FastAPI app with a lifespan that wires the real Redis subscriber
    into the TestClient's event loop.

    FastAPI.lifespan must be passed at construction time; we build a thin wrapper
    around create_app that injects it.  The subscriber background task starts
    inside TestClient.__enter__ (where the lifespan's __aenter__ runs) and is
    cancelled cleanly when TestClient.__exit__ triggers the lifespan's __aexit__.

    The subscriber waits until it has called Redis SUBSCRIBE before yielding so
    the test body can publish immediately without a race.
    """
    settings = Settings(
        database_url=_pg_async_url,
        ws_redis_channel=_WS_CHANNEL,
    )
    # Build the base app first so we can capture ws_manager + ws_session_maker.
    base = create_app(settings)
    ws_manager = base.state.ws_manager
    ws_session_maker = base.state.ws_session_maker

    @asynccontextmanager
    async def _lifespan(fastapi_app):
        sub_client = aioredis.from_url(_redis_url, decode_responses=False)
        subscriber = RedisFanoutSubscriber(
            sub_client,
            ws_session_maker,
            ws_manager,
            channel=_WS_CHANNEL,
        )
        stop_event = asyncio.Event()
        task = asyncio.create_task(subscriber.run(stop_event=stop_event))

        # Block until the subscriber has actually called SUBSCRIBE so the test
        # body can publish immediately without a publish-before-subscribe race.
        for _ in range(100):
            if subscriber.subscribed:
                break
            await asyncio.sleep(0.05)

        yield  # ── TestClient body runs here ──

        stop_event.set()
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, TimeoutError):
                pass
        await sub_client.aclose()

    # Rebuild with the lifespan injected.
    from fastapi import FastAPI

    a = FastAPI(title="Factory Monitor API", version="1.0.0", lifespan=_lifespan)
    # Re-register all routers and state from the base app.
    from cloud.api.routes import router
    from cloud.api.twilio_webhook import webhook_router
    from cloud.api.ws import ws_router

    a.include_router(router)
    a.include_router(webhook_router)
    a.include_router(ws_router)
    a.dependency_overrides[get_session_maker] = lambda: ws_session_maker
    a.state.ws_manager = ws_manager
    a.state.ws_session_maker = ws_session_maker
    return a


# ── e2e test ──────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_live_full_lifecycle(app, maker, pub_client):
    """Full incident lifecycle delivered over /ws/live via the REAL Redis publish path.

    Flow proven:
      create_incident_from_anomaly → publish_incident_event (Redis PUBLISH) →
      RedisFanoutSubscriber (in TestClient's loop) → ConnectionManager.broadcast →
      WS client receives incident.created

      _make_incident_due + EscalationWorker.run_once → publish_incident_event →
      … same path … → WS client receives incident.tier_advanced

      acknowledge_incident → publish_incident_event →
      … same path … → WS client receives incident.updated

    Assertions: correct IncidentView shape, monotonic seq, server_now present.
    """
    session_maker = maker
    event = _make_anomaly_event()

    with TestClient(app) as http:
        with _ws_session(http, "/ws/live") as ws:
            # ── 1. snapshot on connect (no incidents yet) ──────────────────────
            snap = _drain_until(ws, "snapshot", timeout=5.0)
            assert snap["version"] == 1
            assert snap.get("server_now"), "snapshot must carry server_now"
            assert isinstance(snap["data"]["incidents"], list)
            last_seq = snap["seq"]

            # ── 2. create an incident via the REAL ingest service ──────────────
            async with session_maker() as s:
                res = await create_incident_from_anomaly(
                    s, event, grace_seconds=5, on_call_resolver=_resolver
                )
                await s.commit()
            assert res.created is True
            incident_id = res.incident_id

            # Publish the CHANGE_CREATED event to Redis → subscriber broadcasts.
            ok = await publish_incident_event(
                pub_client,
                _WS_CHANNEL,
                incident_change(CHANGE_CREATED, incident_id),
            )
            assert ok is True, "Redis publish must succeed"

            created = _drain_until(ws, "incident.created", timeout=8.0)
            assert created["seq"] > last_seq, "seq must be monotonic-increasing"
            assert created.get("server_now"), "incident.created must carry server_now"
            view = created["data"]
            assert view["incident_id"] == str(incident_id)
            assert view["camera_id"] == "cam_e2e_07"
            assert view["zone_id"] == "zone_weld_bay"
            assert view["rule_id"] == "PPE_NO_HARDHAT"
            assert view["anomaly_type"] == "ppe_no_hardhat"
            assert view["status"] == "AWAITING_OPERATOR"
            assert view["current_tier"] == 0
            assert view["tier_label"] == "Operator"
            assert view["deadline_at"] is not None
            last_seq = created["seq"]

            # ── 3. advance tier via REAL escalation worker ─────────────────────
            await _make_incident_due(session_maker, incident_id)
            worker = EscalationWorker(
                session_maker=session_maker,
                worker_id="ws-e2e-worker",
                lease_seconds=30,
                batch_size=10,
            )
            fired = await worker.run_once()
            assert incident_id in fired, "escalation worker must fire the due incident"

            # Verify DB state before publishing.
            async with session_maker() as s:
                inc = await s.get(Incident, incident_id)
                assert inc.status == IncidentStatus.TIER1
                assert inc.current_tier == 1
                assert inc.deadline_at is not None

            # Publish tier-advanced event over the REAL Redis path.
            ok = await publish_incident_event(
                pub_client,
                _WS_CHANNEL,
                incident_change(CHANGE_TIER_ADVANCED, incident_id),
            )
            assert ok is True

            adv = _drain_until(ws, "incident.tier_advanced", timeout=8.0)
            assert adv["seq"] > last_seq, "tier_advanced seq must be > created seq"
            assert adv.get("server_now"), "incident.tier_advanced must carry server_now"
            assert adv["data"]["incident_id"] == str(incident_id)
            assert adv["data"]["current_tier"] == 1
            assert adv["data"]["status"] == "TIER1"
            assert adv["data"]["deadline_at"] is not None, (
                "tier_advanced re-anchors the timer → deadline_at must be non-null"
            )
            last_seq = adv["seq"]

            # ── 4. acknowledge via REAL shared incident-action service ──────────
            async with session_maker() as s:
                await _ack_incident(s, incident_id)
                await s.commit()

            # Verify ACK state before publishing.
            async with session_maker() as s:
                inc = await s.get(Incident, incident_id)
                assert inc.status == IncidentStatus.ACK
                assert inc.next_fire_at is None
                assert inc.deadline_at is None

            # Publish updated event over the REAL Redis path.
            ok = await publish_incident_event(
                pub_client,
                _WS_CHANNEL,
                incident_change(CHANGE_UPDATED, incident_id),
            )
            assert ok is True

            updated = _drain_until(ws, "incident.updated", "incident.resolved", timeout=8.0)
            assert updated["seq"] > last_seq, "updated seq must be > tier_advanced seq"
            assert updated.get("server_now"), "incident.updated must carry server_now"
            data = updated["data"]
            assert data["incident_id"] == str(incident_id)
            assert data["status"] == "ACK"
            assert data["deadline_at"] is None, (
                "ACK clears deadline_at → browser timer must stop"
            )
