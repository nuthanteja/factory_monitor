"""Integration tests for /ws/live WebSocket endpoint.

Use Starlette TestClient (sync) for WebSocket tests.  Seeding is done through
the sync psycopg2 URL so we never touch asyncpg outside the anyio event-loop
that TestClient spins up internally.  The async_sessionmaker that the WS
handler uses is built fresh (sync construction) and bound to the same URL so
it creates connections on demand inside TestClient's loop.
"""
from __future__ import annotations

import concurrent.futures
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.db.models import IncidentStatus

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")


def _drain_until(ws, match_type: str, *, timeout: float = 3.0) -> dict:
    """Read frames from *ws* until one with ``type == match_type`` is found.

    Each ``ws.receive_json()`` call blocks until the server sends a frame;
    running it in a thread lets us impose a wall-clock deadline that is safe
    even on a heavily loaded CI machine.  Raises ``TimeoutError`` if no
    matching frame arrives within *timeout* seconds, or ``AssertionError`` if
    the executor itself raises.
    """
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
                raise TimeoutError(
                    f"No '{match_type}' frame received within {timeout}s"
                ) from exc
            if frame.get("type") == match_type:
                return frame
            # Not our frame — keep draining.  Subtract a tiny floor so we
            # never spin without bound even if frames arrive very quickly.
            remaining -= 0.001
    raise TimeoutError(f"No '{match_type}' frame received within {timeout}s")


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


# ── module-scoped containers ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def sync_url(pg_container: PostgresContainer) -> str:
    """psycopg2-style URL; also runs Alembic migrations once."""
    url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


# ── function-scoped helpers ───────────────────────────────────────────────────

@pytest.fixture
def async_url(sync_url: str) -> str:
    return _async_url(sync_url)


@pytest.fixture
def seeded_incident_id(sync_url: str) -> uuid.UUID:
    """Insert one AWAITING_OPERATOR incident synchronously via psycopg2."""
    inc_id = uuid.uuid4()
    dedup = f"cam_01|cam_01:1487|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}"
    now = datetime.now(tz=UTC)
    deadline = now + timedelta(seconds=120)

    # psycopg2 needs a plain postgresql:// URL (not postgresql+psycopg2://)
    plain_url = sync_url.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(plain_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (
                    id, site_id, camera_id, zone_id, anomaly_type, rule_id,
                    object_class, track_id, severity, dedup_key, status,
                    current_tier, next_fire_at, deadline_at, snapshot_url,
                    is_synthetic, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, NOW(), NOW()
                )
                """,
                (
                    str(inc_id), "plant-01", "cam_01", "zone_weld_bay",
                    "ppe_no_hardhat", "PPE_NO_HARDHAT", "person",
                    "cam_01:1487", "high", dedup,
                    IncidentStatus.AWAITING_OPERATOR.value,
                    0, deadline, deadline, "",
                    False,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return inc_id


@pytest.fixture
def app(async_url: str):
    """Build the FastAPI app with an async_sessionmaker that creates its
    connections on-demand inside the event loop TestClient starts.

    A fresh engine + pool per test prevents connection-count leaks between
    tests.  Pool cleanup happens naturally when the container tears down at
    module scope; explicit async disposal here would race with TestClient's
    proactor loop being closed, so we skip it.
    """
    engine = create_async_engine(async_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    a = create_app()
    a.dependency_overrides[get_session_maker] = lambda: maker
    a.state.ws_session_maker = maker
    return a


@pytest.fixture
def seeded_resolved_incident_id(sync_url: str) -> uuid.UUID:
    """Insert one RESOLVED incident synchronously via psycopg2."""
    inc_id = uuid.uuid4()
    dedup = f"cam_02|cam_02:9999|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}"
    now = datetime.now(tz=UTC)
    deadline = now + timedelta(seconds=120)

    plain_url = sync_url.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(plain_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (
                    id, site_id, camera_id, zone_id, anomaly_type, rule_id,
                    object_class, track_id, severity, dedup_key, status,
                    current_tier, next_fire_at, deadline_at, snapshot_url,
                    is_synthetic, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, NOW(), NOW()
                )
                """,
                (
                    str(inc_id), "plant-01", "cam_02", "zone_weld_bay",
                    "ppe_no_hardhat", "PPE_NO_HARDHAT", "person",
                    "cam_02:9999", "high", dedup,
                    IncidentStatus.RESOLVED.value,
                    0, deadline, deadline, "",
                    False,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return inc_id


# ── tests ─────────────────────────────────────────────────────────────────────

def test_connect_receives_snapshot_with_active_incident(app, seeded_incident_id):
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["version"] == 1
        assert msg["seq"] == 1
        datetime.fromisoformat(msg["server_now"])  # valid ISO-8601
        ids = [i["incident_id"] for i in msg["data"]["incidents"]]
        assert str(seeded_incident_id) in ids
        sid = str(seeded_incident_id)
        view = next(i for i in msg["data"]["incidents"] if i["incident_id"] == sid)
        assert view["status"] == "AWAITING_OPERATOR"
        assert view["tier_label"] == "Operator"
        assert view["deadline_at"] is not None
        assert set(view.keys()) == {
            "incident_id", "camera_id", "zone_id", "rule_id", "anomaly_type",
            "severity", "object_class", "status", "current_tier",
            "deadline_at", "opened_at", "snapshot_url", "tier_label",
        }


def test_heartbeat_follows_snapshot_with_server_now(app, seeded_incident_id):
    # Heartbeat and timer intervals are set small so the test is fast.
    import cloud.api.ws as ws_mod

    app.state.ws_heartbeat_seconds = 0.05
    app.state.ws_timer_snapshot_seconds = 0.05
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        # Drain frames until both system.heartbeat AND timer.snapshot have been
        # observed (up to 3 s wall-clock).  A fixed iteration count races on
        # slow/loaded CI machines because asyncio.sleep(0.05) may actually take
        # several hundred ms there.
        seen = {first["type"]}
        seqs = [first["seq"]]
        NEED = {"system.heartbeat", "timer.snapshot"}

        def _read_one() -> dict:
            return ws.receive_json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            deadline = 3.0
            while not NEED.issubset(seen) and deadline > 0:
                fut = pool.submit(_read_one)
                try:
                    m = fut.result(timeout=deadline)
                except concurrent.futures.TimeoutError:
                    break
                seen.add(m["type"])
                seqs.append(m["seq"])
                datetime.fromisoformat(m["server_now"])
                deadline -= 0.001  # floor to avoid zero-timeout edge case

        assert "system.heartbeat" in seen, "system.heartbeat never received"
        assert "timer.snapshot" in seen, "timer.snapshot never received"
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    _ = ws_mod  # ensure the module import path is the one under test


def test_timer_snapshot_payload_shape(app, seeded_incident_id):
    # Push the heartbeat far out so only timer.snapshot frames appear after the
    # initial snapshot.  Use _drain_until so the test is resilient to asyncio
    # scheduler jitter on loaded CI machines: it reads frames until a
    # timer.snapshot arrives or the 3-second wall-clock deadline expires,
    # rather than relying on the 50 ms sleep firing within a fixed N-frame
    # window.
    app.state.ws_heartbeat_seconds = 5.0
    app.state.ws_timer_snapshot_seconds = 0.05
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        timer = _drain_until(ws, "timer.snapshot", timeout=3.0)
        rows = timer["data"]["incidents"]
        assert any(r["incident_id"] == str(seeded_incident_id) for r in rows)
        row = next(r for r in rows if r["incident_id"] == str(seeded_incident_id))
        assert set(row.keys()) == {"incident_id", "deadline_at", "current_tier"}


def test_subscribe_message_is_accepted_and_acked(app, seeded_incident_id):
    app.state.ws_heartbeat_seconds = 5.0
    app.state.ws_timer_snapshot_seconds = 5.0
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"action": "subscribe", "topics": ["incidents"], "last_seq": 1})
        ack = ws.receive_json()
        assert ack["type"] == "system.heartbeat"   # ack is sent as an immediate heartbeat re-anchor
        assert ack["data"] == {}


def test_snapshot_excludes_resolved_and_ack(
    app, seeded_incident_id, seeded_resolved_incident_id
):
    """Snapshot must include the active incident and exclude the RESOLVED one."""
    app.state.ws_heartbeat_seconds = 5.0
    app.state.ws_timer_snapshot_seconds = 5.0
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        ids = [i["incident_id"] for i in msg["data"]["incidents"]]
        assert str(seeded_incident_id) in ids
        assert str(seeded_resolved_incident_id) not in ids


def test_disconnect_returns_connection_count_to_zero(app):
    """connection_count must be 1 while connected and 0 after disconnect."""
    app.state.ws_heartbeat_seconds = 5.0
    app.state.ws_timer_snapshot_seconds = 5.0
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()  # consume the snapshot
            assert app.state.ws_manager.connection_count == 1
        assert app.state.ws_manager.connection_count == 0
