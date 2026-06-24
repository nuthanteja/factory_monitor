"""Integration test: create_incident_from_anomaly enqueues tier-0 outbox row
atomically in the same transaction when an on-call resolver + tier config exist."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Outbox
from cloud.common.on_call_resolver import resolve
from cloud.common.schemas.anomaly import AnomalyEvent
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.ingest_worker.service import create_incident_from_anomaly

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
async def maker(migrated_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    await seed_demo_roster(m)
    await seed_demo_tiers(m, site_id="plant-01", delay_seconds=5)
    yield m
    await engine.dispose()


def _make_event(**overrides) -> AnomalyEvent:
    base = dict(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        occurred_at=datetime(2026, 6, 22, 10, 15, 3, 412000, tzinfo=UTC),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        track_id="cam_01:1487",
        object_class="person",
        severity="high",
        confidence=0.91,
        dedup_key=f"cam_outbox|cam_01:1487|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
        evidence={"bbox": [880, 412, 130, 348], "snapshot_url": "", "footage_source": "clip"},
        source="edge",
    )
    base.update(overrides)
    return AnomalyEvent(**base)


@pytest.mark.asyncio
async def test_create_incident_enqueues_tier0_outbox_row(maker):
    event = _make_event()
    async with maker() as s:
        result = await create_incident_from_anomaly(
            s, event, grace_seconds=5, on_call_resolver=resolve
        )
        await s.commit()

    assert result.created is True

    async with maker() as s:
        outbox_rows = (
            await s.execute(
                select(Outbox).where(Outbox.incident_id == result.incident_id)
            )
        ).scalars().all()

    assert len(outbox_rows) == 1
    row = outbox_rows[0]
    assert row.tier == 0
    assert row.kind == "TEMPLATE"
    assert row.to_phone_e164 == "+15550000001"  # Demo Operator phone from seed
    assert row.status == "PENDING"
    assert row.idempotency_key == f"{result.incident_id}|0"
    assert row.template_name == "operator_alert_v1"


@pytest.mark.asyncio
async def test_create_incident_without_resolver_has_no_outbox_row(maker):
    """Calling without on_call_resolver (old signature) must not enqueue outbox."""
    event = _make_event(dedup_key=f"cam_noout|x|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}")
    async with maker() as s:
        result = await create_incident_from_anomaly(s, event, grace_seconds=5)
        await s.commit()

    assert result.created is True
    async with maker() as s:
        outbox_count = len(
            (
                await s.execute(
                    select(Outbox).where(Outbox.incident_id == result.incident_id)
                )
            ).scalars().all()
        )
    assert outbox_count == 0


@pytest.mark.asyncio
async def test_tier0_outbox_idempotency_key_is_unique(maker):
    """idempotency_key=incident_id|0 is UNIQUE — duplicate ingest cannot double-enqueue."""
    event = _make_event(dedup_key=f"cam_idem|x|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}")
    async with maker() as s:
        result = await create_incident_from_anomaly(
            s, event, grace_seconds=5, on_call_resolver=resolve
        )
        await s.commit()

    assert result.created is True

    async with maker() as s:
        outbox_rows = (
            await s.execute(
                select(Outbox).where(Outbox.incident_id == result.incident_id)
            )
        ).scalars().all()

    assert len(outbox_rows) == 1
    assert outbox_rows[0].idempotency_key == f"{result.incident_id}|0"


@pytest.mark.asyncio
async def test_tier0_outbox_idempotency_key_db_constraint(maker):
    """DB UNIQUE constraint on idempotency_key must reject a duplicate row at flush time."""
    event = _make_event(dedup_key=f"cam_dbcon|x|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}")
    async with maker() as s:
        result = await create_incident_from_anomaly(
            s, event, grace_seconds=5, on_call_resolver=resolve
        )
        await s.commit()

    assert result.created is True
    incident_id = result.incident_id
    duplicate_key = f"{incident_id}|0"

    async with maker() as s:
        dup = Outbox(
            id=uuid.uuid4(),
            incident_id=incident_id,
            tier=0,
            to_phone_e164="+15550000001",
            channel="console",
            kind="TEMPLATE",
            template_name="operator_alert_v1",
            idempotency_key=duplicate_key,
            status="PENDING",
            attempts=0,
            max_attempts=6,
        )
        s.add(dup)
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()
