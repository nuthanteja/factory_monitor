from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.config import Settings
from cloud.common.db.models import Incident, IncidentStatus

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest_asyncio.fixture
async def session_maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_incident_id(session_maker):
    inc_id = uuid.uuid4()
    _deadline = datetime.now(tz=UTC) + timedelta(hours=1)
    async with session_maker() as s:
        s.add(
            Incident(
                id=inc_id,
                site_id="plant-01",
                camera_id="cam_01",
                zone_id="zone_weld_bay",
                anomaly_type="ppe_no_hardhat",
                rule_id="PPE_NO_HARDHAT",
                object_class="person",
                track_id="cam_01:1487",
                severity="high",
                dedup_key=f"cam_01|cam_01:1487|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
                status=IncidentStatus.AWAITING_OPERATOR,
                current_tier=0,
                next_fire_at=datetime.now(tz=UTC) + timedelta(seconds=120),
                deadline_at=_deadline,
                snapshot_url="",
                is_synthetic=False,
            )
        )
        await s.commit()
    return inc_id, _deadline


@pytest_asyncio.fixture
async def client(session_maker):
    app = create_app(Settings(ws_fanout_enabled=False))
    app.dependency_overrides[get_session_maker] = lambda: session_maker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_list_incidents_returns_seeded_incident(client, seeded_incident_id):
    inc_id, expected_deadline = seeded_incident_id
    resp = await client.get("/api/v1/incidents")
    assert resp.status_code == 200
    body = resp.json()

    assert "incidents" in body and "meta" in body
    assert "server_now" in body["meta"]
    datetime.fromisoformat(body["meta"]["server_now"])  # must be valid ISO-8601

    match = [i for i in body["incidents"] if i["id"] == str(inc_id)]
    assert len(match) == 1
    inc = match[0]
    # --- existing fields unchanged (regression guard) ---
    assert inc["camera_id"] == "cam_01"
    assert inc["zone_id"] == "zone_weld_bay"
    assert inc["anomaly_type"] == "ppe_no_hardhat"
    assert inc["rule_id"] == "PPE_NO_HARDHAT"
    assert inc["severity"] == "high"
    assert inc["status"] == "AWAITING_OPERATOR"
    assert inc["current_tier"] == 0
    assert "created_at" in inc
    assert inc["snapshot_url"] is None  # empty string "" must be coerced to null
    # --- new fields ---
    assert inc["object_class"] == "person"
    assert inc["tier_label"] == "Operator"  # tier 0 + AWAITING_OPERATOR → "Operator"
    assert inc["deadline_at"] is not None
    deadline_parsed = datetime.fromisoformat(inc["deadline_at"].replace("Z", "+00:00"))
    assert abs((deadline_parsed - expected_deadline).total_seconds()) < 2
    # --- exact key set (guards against accidental additions/renames) ---
    assert set(inc.keys()) == {
        "id", "camera_id", "zone_id", "anomaly_type", "rule_id",
        "severity", "status", "current_tier", "created_at", "snapshot_url",
        "object_class", "deadline_at", "tier_label",
    }
