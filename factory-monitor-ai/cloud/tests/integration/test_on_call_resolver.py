"""Integration tests for the on-call resolver against a real Postgres instance."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import OnCallAssignment, User
from cloud.common.on_call_resolver import resolve

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
    yield m
    await engine.dispose()


async def _seed_user(maker: async_sessionmaker, phone: str, role: str = "OPERATOR") -> User:
    u = User(
        id=uuid.uuid4(),
        site_id="plant-01",
        full_name=f"User {phone}",
        phone_e164=phone,
        role=role,
        is_active=True,
    )
    async with maker() as s:
        s.add(u)
        await s.commit()
        await s.refresh(u)
    return u


async def _seed_assignment(
    maker: async_sessionmaker,
    user_id: uuid.UUID,
    role: str,
    starts_at: datetime,
    ends_at: datetime,
    zone_id: str | None = None,
) -> None:
    a = OnCallAssignment(
        id=uuid.uuid4(),
        site_id="plant-01",
        role=role,
        zone_id=zone_id,
        user_id=user_id,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    async with maker() as s:
        s.add(a)
        await s.commit()


@pytest.mark.asyncio
async def test_resolve_returns_user_in_window(maker):
    now = datetime.now(timezone.utc)
    user = await _seed_user(maker, "+10000000001")
    await _seed_assignment(
        maker, user.id, "OPERATOR",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )
    async with maker() as s:
        result = await resolve(s, role="OPERATOR", site_id="plant-01", zone_id=None, at=now)
    assert result is not None
    assert result.phone_e164 == "+10000000001"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_assignment(maker):
    now = datetime.now(timezone.utc)
    async with maker() as s:
        result = await resolve(s, role="PLANT_DIRECTOR", site_id="plant-01", zone_id="zone_x", at=now)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_zone_specific_before_plant_wide(maker):
    now = datetime.now(timezone.utc)
    plant_wide_user = await _seed_user(maker, "+10000000002")
    zone_user = await _seed_user(maker, "+10000000003")
    await _seed_assignment(
        maker, plant_wide_user.id, "FLOOR_MANAGER",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        zone_id=None,  # plant-wide
    )
    await _seed_assignment(
        maker, zone_user.id, "FLOOR_MANAGER",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        zone_id="zone_weld_bay",  # zone-specific wins
    )
    async with maker() as s:
        result = await resolve(s, role="FLOOR_MANAGER", site_id="plant-01", zone_id="zone_weld_bay", at=now)
    assert result is not None
    assert result.phone_e164 == "+10000000003"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_plant_wide_when_no_zone_match(maker):
    now = datetime.now(timezone.utc)
    plant_wide_user = await _seed_user(maker, "+10000000004")
    await _seed_assignment(
        maker, plant_wide_user.id, "FLOOR_MANAGER",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        zone_id=None,
    )
    async with maker() as s:
        result = await resolve(s, role="FLOOR_MANAGER", site_id="plant-01", zone_id="zone_nonexistent", at=now)
    assert result is not None
    assert result.phone_e164 == "+10000000004"


@pytest.mark.asyncio
async def test_resolve_expired_assignment_not_returned(maker):
    now = datetime.now(timezone.utc)
    user = await _seed_user(maker, "+10000000005")
    await _seed_assignment(
        maker, user.id, "PLANT_DIRECTOR",
        starts_at=now - timedelta(hours=3),
        ends_at=now - timedelta(hours=1),  # already expired
    )
    async with maker() as s:
        result = await resolve(s, role="PLANT_DIRECTOR", site_id="plant-01", zone_id=None, at=now)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_at_starts_at_boundary_is_inclusive(maker):
    """Verify [starts_at, ends_at) half-open window: starts_at is inclusive."""
    now = datetime.now(timezone.utc)
    user = await _seed_user(maker, "+10000000006")
    starts_at = now
    ends_at = now + timedelta(hours=2)
    await _seed_assignment(
        maker, user.id, "OPERATOR",
        starts_at=starts_at,
        ends_at=ends_at,
        zone_id=None,  # plant-wide to avoid fallback conflicts
    )
    async with maker() as s:
        result = await resolve(s, role="OPERATOR", site_id="plant-01", zone_id=None, at=starts_at)
    assert result is not None
    assert result.phone_e164 == "+10000000006"


@pytest.mark.asyncio
async def test_resolve_at_ends_at_boundary_is_exclusive(maker):
    """Verify [starts_at, ends_at) half-open window: ends_at is exclusive."""
    now = datetime.now(timezone.utc)
    user = await _seed_user(maker, "+10000000007")
    starts_at = now - timedelta(hours=1)
    ends_at = now
    await _seed_assignment(
        maker, user.id, "PLANT_DIRECTOR",
        starts_at=starts_at,
        ends_at=ends_at,
        zone_id=None,  # plant-wide to avoid fallback conflicts
    )
    async with maker() as s:
        result = await resolve(s, role="PLANT_DIRECTOR", site_id="plant-01", zone_id=None, at=ends_at)
    assert result is None
