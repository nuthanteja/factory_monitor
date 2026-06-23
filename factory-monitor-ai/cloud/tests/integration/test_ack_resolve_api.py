from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from alembic.config import Config

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus

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
async def maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


@pytest_asyncio.fixture
async def client(maker):
    app = create_app()
    app.dependency_overrides[get_session_maker] = lambda: maker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_incident(status: IncidentStatus = IncidentStatus.AWAITING_OPERATOR) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_01:1001",
        severity="high",
        dedup_key=f"cam_01|cam_01:1001|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
        status=status,
        current_tier=0,
        next_fire_at=datetime.now(tz=timezone.utc) + timedelta(seconds=120),
        snapshot_url="",
        is_synthetic=False,
    )


# ── acknowledge ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledge_sets_ack_status_and_clears_timer(client, maker):
    inc = _seed_incident()
    async with maker() as s:
        s.add(inc)
        await s.commit()

    idem_key = str(uuid.uuid4())
    resp = await client.post(
        f"/api/v1/incidents/{inc.id}/acknowledge",
        headers={"Idempotency-Key": idem_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == str(inc.id)
    assert body["status"] == "ACK"

    async with maker() as s:
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.ACK
        assert row.next_fire_at is None
        assert row.acked_at is not None
        evt = (
            await s.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == inc.id)
                .where(IncidentEvent.type == "ACK")
            )
        ).scalar_one()
        assert evt.from_state == "AWAITING_OPERATOR"
        assert evt.to_state == "ACK"


@pytest.mark.asyncio
async def test_acknowledge_is_idempotent(client, maker):
    """Sending the same Idempotency-Key twice must return 200 both times."""
    inc = _seed_incident()
    async with maker() as s:
        s.add(inc)
        await s.commit()

    idem_key = str(uuid.uuid4())
    r1 = await client.post(
        f"/api/v1/incidents/{inc.id}/acknowledge",
        headers={"Idempotency-Key": idem_key},
    )
    r2 = await client.post(
        f"/api/v1/incidents/{inc.id}/acknowledge",
        headers={"Idempotency-Key": idem_key},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["status"] == "ACK"
    assert r2.json()["status"] == "ACK"

    async with maker() as s:
        # Only one ACK audit row despite two calls
        rows = (
            await s.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == inc.id)
                .where(IncidentEvent.type == "ACK")
            )
        ).scalars().all()
        assert len(rows) == 1
        # Assert incident DB state: status ACK, timer cleared
        inc_row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert inc_row.status == IncidentStatus.ACK
        assert inc_row.next_fire_at is None


@pytest.mark.asyncio
async def test_acknowledge_already_resolved_returns_409(client, maker):
    inc = _seed_incident(status=IncidentStatus.RESOLVED)
    async with maker() as s:
        inc.next_fire_at = None
        s.add(inc)
        await s.commit()

    resp = await client.post(f"/api/v1/incidents/{inc.id}/acknowledge")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_acknowledge_unknown_incident_returns_404(client, maker):
    resp = await client.post(f"/api/v1/incidents/{uuid.uuid4()}/acknowledge")
    assert resp.status_code == 404


# ── resolve ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_sets_resolved_status_and_note(client, maker):
    inc = _seed_incident()
    async with maker() as s:
        s.add(inc)
        await s.commit()

    resp = await client.post(
        f"/api/v1/incidents/{inc.id}/resolve",
        json={"resolution_note": "PPE worn; false positive on re-inspection."},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "RESOLVED"

    async with maker() as s:
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.RESOLVED
        assert row.next_fire_at is None
        assert row.resolved_at is not None
        assert row.resolution_note == "PPE worn; false positive on re-inspection."


@pytest.mark.asyncio
async def test_resolve_from_ack_state_succeeds(client, maker):
    inc = _seed_incident(status=IncidentStatus.ACK)
    async with maker() as s:
        inc.next_fire_at = None
        inc.acked_at = datetime.now(tz=timezone.utc)
        s.add(inc)
        await s.commit()

    resp = await client.post(
        f"/api/v1/incidents/{inc.id}/resolve",
        json={"resolution_note": "Closed after ack."},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RESOLVED"

    async with maker() as s:
        # Assert incident DB state: status RESOLVED, timer cleared
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.RESOLVED
        assert row.next_fire_at is None
        # Assert RESOLVED audit row exists
        evt = (
            await s.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == inc.id)
                .where(IncidentEvent.type == "RESOLVED")
            )
        ).scalar_one()
        assert evt is not None


@pytest.mark.asyncio
async def test_resolve_unknown_incident_returns_404(client, maker):
    resp = await client.post(f"/api/v1/incidents/{uuid.uuid4()}/resolve")
    assert resp.status_code == 404
