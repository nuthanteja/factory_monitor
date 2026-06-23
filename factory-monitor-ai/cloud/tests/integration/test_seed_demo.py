"""Integration tests confirming demo seed creates the expected roster + tiers."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import EscalationTier, OnCallAssignment, User
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers

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


@pytest.mark.asyncio
async def test_seed_creates_three_users(maker):
    async with maker() as s:
        count = (await s.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 3


@pytest.mark.asyncio
async def test_seed_creates_three_on_call_assignments(maker):
    async with maker() as s:
        count = (await s.execute(select(func.count()).select_from(OnCallAssignment))).scalar_one()
    assert count == 3


@pytest.mark.asyncio
async def test_seed_creates_three_tiers(maker):
    async with maker() as s:
        tiers = (await s.execute(select(EscalationTier).order_by(EscalationTier.tier))).scalars().all()
    assert len(tiers) == 3
    assert tiers[0].tier == 0
    assert tiers[0].role == "OPERATOR"
    assert tiers[1].tier == 1
    assert tiers[1].role == "FLOOR_MANAGER"
    assert tiers[2].tier == 2
    assert tiers[2].role == "PLANT_DIRECTOR"


@pytest.mark.asyncio
async def test_seed_tiers_have_correct_delay(maker):
    async with maker() as s:
        tiers = (await s.execute(select(EscalationTier))).scalars().all()
    for t in tiers:
        assert t.delay_seconds == 5


@pytest.mark.asyncio
async def test_seed_is_idempotent(maker):
    """Re-running seed must not duplicate rows."""
    await seed_demo_roster(maker)
    await seed_demo_tiers(maker, site_id="plant-01", delay_seconds=5)
    async with maker() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        assignments = (await s.execute(select(func.count()).select_from(OnCallAssignment))).scalar_one()
        tiers = (await s.execute(select(func.count()).select_from(EscalationTier))).scalar_one()
    assert users == 3
    assert assignments == 3
    assert tiers == 3
