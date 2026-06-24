"""Integration tests for /ws/live WebSocket endpoint.

Use Starlette TestClient (sync) for WebSocket tests.  Seeding is done through
the sync psycopg2 URL so we never touch asyncpg outside the anyio event-loop
that TestClient spins up internally.  The async_sessionmaker that the WS
handler uses is built fresh (sync construction) and bound to the same URL so
it creates connections on demand inside TestClient's loop.
"""
from __future__ import annotations

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
    connections on-demand inside the event loop TestClient starts."""
    engine = create_async_engine(async_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    a = create_app()
    a.dependency_overrides[get_session_maker] = lambda: maker
    a.state.ws_session_maker = maker
    return a


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
    # Heartbeat interval is overridden tiny so the test is fast.
    import cloud.api.ws as ws_mod

    app.state.ws_heartbeat_seconds = 0.05
    app.state.ws_timer_snapshot_seconds = 0.05
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        # Drain a few frames; assert a heartbeat and a timer.snapshot appear,
        # all carry server_now, and seq is strictly monotonic.
        seen = {first["type"]}
        seqs = [first["seq"]]
        for _ in range(6):
            m = ws.receive_json()
            seen.add(m["type"])
            seqs.append(m["seq"])
            datetime.fromisoformat(m["server_now"])
        assert "system.heartbeat" in seen
        assert "timer.snapshot" in seen
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    _ = ws_mod  # ensure the module import path is the one under test


def test_timer_snapshot_payload_shape(app, seeded_incident_id):
    app.state.ws_heartbeat_seconds = 5.0          # push heartbeats out of the way
    app.state.ws_timer_snapshot_seconds = 0.05
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        timer = None
        for _ in range(6):
            m = ws.receive_json()
            if m["type"] == "timer.snapshot":
                timer = m
                break
        assert timer is not None
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
