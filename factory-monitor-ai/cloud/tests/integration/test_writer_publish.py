"""Writers publish a compact change AFTER commit, and a publish failure never
breaks the transaction (design §3.2 commit-order, §8 Redis-down)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.incident_actions import acknowledge_incident, resolve_incident
from cloud.common.ws_events import CHANGE_RESOLVED, CHANGE_UPDATED

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


def _seed(status=IncidentStatus.AWAITING_OPERATOR) -> Incident:
    return Incident(
        id=uuid.uuid4(), site_id="plant-01", camera_id="cam_01",
        zone_id="zone_weld_bay", anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT", object_class="person", track_id="cam_01:1",
        severity="high", dedup_key=f"k|{uuid.uuid4().hex}|PPE|b",
        status=status, current_tier=0,
        next_fire_at=datetime.now(UTC) + timedelta(seconds=120),
        snapshot_url="", is_synthetic=False,
    )


class _Recorder:
    def __init__(self) -> None:
        self.changes: list[dict] = []

    async def __call__(self, change: dict) -> None:
        self.changes.append(change)


class _Boom:
    async def __call__(self, change: dict) -> None:
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_ack_publishes_updated_after_commit(maker):
    from cloud.api.routes import _publish_after  # helper added in this task

    inc = _seed()
    async with maker() as s:
        s.add(inc)
        await s.commit()

    rec = _Recorder()
    async with maker() as s:
        updated = await acknowledge_incident(s, inc.id)
        await s.commit()
    await _publish_after(rec, CHANGE_UPDATED, inc.id, status=updated.status.value)

    assert rec.changes == [
        {"change_type": "incident.updated", "incident_id": str(inc.id), "status": "ACK"}
    ]


@pytest.mark.asyncio
async def test_resolve_publish_failure_does_not_break_commit(maker):
    from cloud.api.routes import _publish_after

    inc = _seed()
    async with maker() as s:
        s.add(inc)
        await s.commit()

    async with maker() as s:
        await resolve_incident(s, inc.id, resolution_note="done")
        await s.commit()  # txn already durable

    # Exploding publisher must be swallowed by _publish_after (best-effort).
    await _publish_after(_Boom(), CHANGE_RESOLVED, inc.id)

    # State is committed regardless of the publish blowing up.
    async with maker() as s:
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.RESOLVED
