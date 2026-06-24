"""Redis-down fallback: the Postgres poll re-broadcasts incidents changed since
the watermark as incident.updated (design §8 'Redis down').

The FakeManager mirrors the real ConnectionManager.broadcast(type, data) 2-arg
signature — the manager owns envelope framing and per-connection seq.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.ws.contract import WsType
from cloud.common.ws.fallback import PostgresPollFallback, poll_changes_once

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
    """Stand-in for ConnectionManager — records broadcast(WsType, data) calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[WsType, dict]] = []

    async def broadcast(self, type: WsType, data: dict) -> int:  # noqa: A002
        self.calls.append((type, data))
        return 1


async def _insert(maker: async_sessionmaker, dedup: str) -> Incident:
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
        dedup_key=dedup,
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
async def test_poll_broadcasts_rows_changed_since_watermark(maker):
    """poll_changes_once picks up rows with updated_at > since and broadcasts them."""
    since = datetime.now(UTC) - timedelta(seconds=1)
    inc = await _insert(maker, f"k|{uuid.uuid4().hex}|PPE|b")
    mgr = FakeManager()

    count, new_wm = await poll_changes_once(maker, mgr, since=since, batch=200)

    assert count >= 1
    ids = {data["incident_id"] for (_type, data) in mgr.calls}
    assert str(inc.id) in ids
    # Fallback always broadcasts as INCIDENT_UPDATED (full re-sync for degraded path)
    assert all(ws_type is WsType.INCIDENT_UPDATED for (ws_type, _data) in mgr.calls)
    # Data is an IncidentView dict — no envelope fields
    for _ws_type, data in mgr.calls:
        assert "seq" not in data
        assert "server_now" not in data
    assert new_wm >= inc.updated_at


@pytest.mark.asyncio
async def test_poll_is_incremental_on_watermark(maker):
    """Watermark advances so already-broadcast rows are never re-sent."""
    # First poll drains existing rows and advances the watermark.
    _, wm1 = await poll_changes_once(
        maker, FakeManager(), since=datetime.now(UTC) - timedelta(seconds=1), batch=200
    )

    # Quiet poll: nothing changed after wm1 → no broadcasts, watermark unchanged.
    mgr_quiet = FakeManager()
    count_quiet, wm2 = await poll_changes_once(maker, mgr_quiet, since=wm1, batch=200)
    assert count_quiet == 0
    assert mgr_quiet.calls == []
    assert wm2 == wm1

    # Touch one incident → next poll from wm2 picks up exactly that row.
    inc = await _insert(maker, f"k|{uuid.uuid4().hex}|PPE|b")
    async with maker() as s:
        await s.execute(
            text("UPDATE incidents SET updated_at = now() WHERE id = :id"),
            {"id": inc.id},
        )
        await s.commit()

    mgr2 = FakeManager()
    count_after, wm3 = await poll_changes_once(maker, mgr2, since=wm2, batch=200)
    assert count_after >= 1
    assert str(inc.id) in {data["incident_id"] for (_t, data) in mgr2.calls}
    assert wm3 >= wm2


@pytest.mark.asyncio
async def test_no_history_replay_on_fresh_start(maker):
    """PostgresPollFallback initialises watermark to now() — pre-existing rows not broadcast."""
    # Insert an incident that exists before the fallback starts.
    await _insert(maker, f"k|{uuid.uuid4().hex}|PPE|b")

    mgr = FakeManager()
    stop = asyncio.Event()
    fallback = PostgresPollFallback(maker, mgr, poll_seconds=0.05, batch=200)

    # Run one iteration then stop immediately.
    async def _run():
        stop.set()  # stop after first sleep
        await fallback.run(stop_event=stop)

    await asyncio.wait_for(_run(), timeout=2.0)

    # The watermark was initialised to "now" on start, so the pre-existing
    # incident (updated_at <= start time) must not have been broadcast.
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_graceful_stop_via_stop_event(maker):
    """PostgresPollFallback exits cleanly when stop_event is set."""
    mgr = FakeManager()
    stop = asyncio.Event()
    fallback = PostgresPollFallback(maker, mgr, poll_seconds=0.05, batch=200)

    task = asyncio.create_task(fallback.run(stop_event=stop))
    await asyncio.sleep(0.15)  # let it run a couple of polls
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    # No exception raised — graceful exit confirmed.
