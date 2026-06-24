"""The supervisor delivers live updates via the poll fallback while Redis is
down, and switches to the Redis subscriber when Redis is healthy (design §8)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.config import Settings
from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.ws.contract import WsType
from cloud.common.ws.fanout import FanoutSupervisor

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


class FakeManager:
    """Stand-in for ConnectionManager.

    Records broadcast(WsType, data) calls and stores them as
    {"type": ws_type.value, "data": data} so tests can assert on envelope shape.
    """

    def __init__(self) -> None:
        self._seq = 0
        self.sent: list[dict] = []

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def broadcast(self, type: WsType, data: dict) -> int:  # noqa: A002
        self.sent.append({"type": type.value, "data": data})
        return 1


class DownRedis:
    """A redis client whose ping always fails (Redis unreachable)."""

    async def ping(self) -> bool:
        raise ConnectionError("redis down")


async def _insert(maker) -> Incident:
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_03",
        zone_id="z",
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_03:1",
        severity="high",
        dedup_key=f"k|{uuid.uuid4().hex}|PPE|b",
        status=IncidentStatus.AWAITING_OPERATOR,
        current_tier=0,
        next_fire_at=now + timedelta(seconds=120),
        deadline_at=now + timedelta(seconds=120),
        snapshot_url="",
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


@pytest.mark.asyncio
async def test_supervisor_uses_poll_fallback_when_redis_down(maker):
    settings = Settings(ws_fallback_poll_seconds=0.1, ws_fallback_batch=200)
    mgr = FakeManager()
    sup = FanoutSupervisor(DownRedis(), maker, mgr, settings)

    stop = asyncio.Event()
    task = asyncio.create_task(sup.run(stop_event=stop))
    try:
        # An incident created AFTER the fallback's watermark must be delivered.
        await asyncio.sleep(0.15)
        inc = await _insert(maker)
        for _ in range(50):
            if any(e["data"]["incident_id"] == str(inc.id) for e in mgr.sent):
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    assert any(e["data"]["incident_id"] == str(inc.id) for e in mgr.sent)
    assert all(e["type"] == "incident.updated" for e in mgr.sent)


@pytest.mark.asyncio
async def test_supervisor_graceful_stop(maker):
    """Graceful stop via stop_event cancels the active loop with no leaked task."""
    settings = Settings(ws_fallback_poll_seconds=0.1, ws_fallback_batch=200)
    mgr = FakeManager()
    sup = FanoutSupervisor(DownRedis(), maker, mgr, settings)

    stop = asyncio.Event()
    task = asyncio.create_task(sup.run(stop_event=stop))
    await asyncio.sleep(0.15)
    stop.set()
    # Must complete cleanly within timeout — no leaked tasks.
    await asyncio.wait_for(task, timeout=5)
    assert task.done()
    assert not task.cancelled()
