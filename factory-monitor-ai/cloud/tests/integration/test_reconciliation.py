"""Reconciliation: a stale-claimed overdue incident is reclaimed and fired.

Phase 3a deliberately has NO special on-boot reconciliation code: a crashed
worker's row keeps its (now-past) claimed_until, and the normal claim subquery
re-selects it via (claimed_until IS NULL OR claimed_until < now()). This proves
"recovery is free".
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.escalation_worker.worker import poll_once

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
    await seed_demo_roster(m)
    await seed_demo_tiers(m, site_id="plant-01", delay_seconds=5)
    yield m
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_claim_is_reclaimed_and_fired(maker: async_sessionmaker):
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id=None,
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_01:stale",
        severity="high",
        dedup_key=f"stale|{uuid.uuid4().hex}|PPE|bucket",
        status=IncidentStatus.AWAITING_OPERATOR,
        current_tier=0,
        next_fire_at=now - timedelta(seconds=30),  # overdue
        deadline_at=now - timedelta(seconds=30),
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        # Simulate a dead worker that claimed the row, then crashed: lease in the past.
        await s.execute(
            text(
                "UPDATE incidents SET claimed_by='dead-worker', "
                "claimed_until = now() - interval '10 seconds' WHERE id = :id"
            ),
            {"id": inc.id},
        )
        await s.commit()

    processed = await poll_once(maker, worker_id="recovery", lease_seconds=30, batch=10)
    assert processed >= 1

    async with maker() as s:
        updated = await s.get(Incident, inc.id)
        events = (
            await s.execute(
                select(IncidentEvent).where(
                    IncidentEvent.incident_id == inc.id,
                    IncidentEvent.type == "TIER1_FIRED",
                )
            )
        ).scalars().all()

    assert updated.status == IncidentStatus.TIER1
    assert updated.claimed_by is None  # claim cleared by the transition
    assert len(events) == 1
