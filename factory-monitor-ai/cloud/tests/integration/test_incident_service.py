from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from alembic.config import Config

from cloud.common.schemas.anomaly import AnomalyEvent
from cloud.common.db.models import Incident, IncidentEvent
from cloud.ingest_worker.service import create_incident_from_anomaly, IncidentResult

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
async def session_factory(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _make_event(**overrides) -> AnomalyEvent:
    base = dict(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        occurred_at=datetime(2026, 6, 22, 10, 15, 3, 412000, tzinfo=timezone.utc),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        track_id="cam_01:1487",
        object_class="person",
        severity="high",
        confidence=0.91,
        dedup_key="cam_01|cam_01:1487|PPE_NO_HARDHAT|28051503",
        evidence={"bbox": [880, 412, 130, 348], "snapshot_url": "", "footage_source": "clip_03"},
        source="edge",
    )
    base.update(overrides)
    return AnomalyEvent(**base)


async def _count(maker: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with maker() as s:
        inc = (await s.execute(select(func.count()).select_from(Incident))).scalar_one()
        evt = (await s.execute(select(func.count()).select_from(IncidentEvent))).scalar_one()
    return inc, evt


@pytest.mark.asyncio
async def test_new_event_creates_one_incident_and_one_event(session_factory):
    event = _make_event()
    async with session_factory() as s:
        result = await create_incident_from_anomaly(s, event, grace_seconds=120)
        await s.commit()

    assert isinstance(result, IncidentResult)
    assert result.created is True
    assert result.reason == "created"
    assert result.incident_id is not None

    async with session_factory() as s:
        inc = (await s.execute(select(Incident).where(Incident.id == result.incident_id))).scalar_one()
        assert inc.status.value == "AWAITING_OPERATOR"
        assert inc.current_tier == 0
        assert inc.dedup_key == event.dedup_key
        assert inc.next_fire_at is not None
        ev = (
            await s.execute(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))
        ).scalar_one()
        assert ev.type == "CREATED"
        assert str(ev.source_event_id) == event.event_id


@pytest.mark.asyncio
async def test_duplicate_open_dedup_key_does_not_create_second_incident(session_factory):
    dk = "cam_01|cam_01:9999|PPE_NO_HARDHAT|28051599"
    e1 = _make_event(dedup_key=dk, track_id="cam_01:9999")
    e2 = _make_event(dedup_key=dk, track_id="cam_01:9999")

    before = await _count(session_factory)
    async with session_factory() as s:
        r1 = await create_incident_from_anomaly(s, e1, grace_seconds=120)
        await s.commit()
    async with session_factory() as s:
        r2 = await create_incident_from_anomaly(s, e2, grace_seconds=120)
        await s.commit()
    after = await _count(session_factory)

    assert r1.created is True
    assert r2.created is False
    assert r2.reason == "duplicate_open_dedup"
    assert after[0] - before[0] == 1
    assert after[1] - before[1] == 1


@pytest.mark.asyncio
async def test_duplicate_event_id_is_noop(session_factory):
    eid = str(uuid.uuid4())
    dk = "cam_01|cam_01:7777|PPE_NO_HARDHAT|28051577"
    e1 = _make_event(event_id=eid, dedup_key=dk, track_id="cam_01:7777")
    e2 = _make_event(event_id=eid, dedup_key=dk + "_other", track_id="cam_01:7777")

    before = await _count(session_factory)
    async with session_factory() as s:
        r1 = await create_incident_from_anomaly(s, e1, grace_seconds=120)
        await s.commit()
    async with session_factory() as s:
        r2 = await create_incident_from_anomaly(s, e2, grace_seconds=120)
        await s.commit()
    after = await _count(session_factory)

    assert r1.created is True
    assert r2.created is False
    assert r2.reason == "duplicate_event_id"
    assert after[0] - before[0] == 1
    assert after[1] - before[1] == 1
