"""The supervisor delivers live updates via the poll fallback while Redis is
down, and switches to the Redis subscriber when Redis is healthy (design §8)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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


class FlappingRedis:
    """Ping raises for the first `down_for` calls, then succeeds.

    Also stubs out pubsub() so that when the supervisor switches to the
    subscriber path, it gets a non-blocking pubsub that immediately returns
    no messages (the test uses a spy on RedisFanoutSubscriber.run instead).
    """

    def __init__(self, down_for: int) -> None:
        self._call_count = 0
        self._down_for = down_for

    async def ping(self) -> bool:
        self._call_count += 1
        if self._call_count <= self._down_for:
            raise ConnectionError("redis down (flapping)")
        return True

    def pubsub(self) -> object:  # pragma: no cover — only reached in subscriber path
        class _FakePubSub:
            async def subscribe(self, *_a: object) -> None: ...
            async def get_message(self, **_kw: object) -> None:
                # yield control so the event loop can check stop_event
                await asyncio.sleep(0)
                return None
            async def unsubscribe(self, *_a: object) -> None: ...
            async def aclose(self) -> None: ...

        return _FakePubSub()


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
    # Must complete cleanly within timeout — no leaked tasks (Fix 3: explicit done check).
    await asyncio.wait_for(task, timeout=5)
    assert task.done(), "supervisor task must be done after stop_event"
    assert not task.cancelled(), "supervisor task must exit cleanly, not be cancelled"
    # No pending tasks leaked by the supervisor's inner fallback loop.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert not pending, f"leaked tasks after graceful stop: {pending}"


@pytest.mark.asyncio
async def test_supervisor_recovery_redis_down_then_up(maker):
    """Recovery path: Redis down -> fallback runs; Redis recovers -> subscriber entered.

    FlappingRedis.ping() raises for the first 3 calls (covering the initial
    health check and a supervision window), then succeeds.  We spy on
    RedisFanoutSubscriber.run to detect that the subscriber is entered after
    recovery without actually running a real Redis pubsub loop.
    The supervisor is stopped via stop_event as soon as the spy is triggered
    so the test is fast and deterministic.
    """
    # Small supervision window so the loop re-checks Redis quickly.
    settings = Settings(ws_fallback_poll_seconds=0.05, ws_fallback_batch=200)
    mgr = FakeManager()
    redis = FlappingRedis(down_for=3)

    subscriber_entered = asyncio.Event()

    async def _fake_subscriber_run(self, *, stop_event=None):  # noqa: ANN001
        """Spy: record that the subscriber path was reached, then stop."""
        subscriber_entered.set()
        # Immediately yield so the supervisor can react to stop_event.
        if stop_event is not None:
            stop_event.set()

    stop = asyncio.Event()

    with patch(
        "cloud.common.ws.fanout.RedisFanoutSubscriber.run",
        new=_fake_subscriber_run,
    ):
        sup = FanoutSupervisor(redis, maker, mgr, settings)
        task = asyncio.create_task(sup.run(stop_event=stop))

        # Wait for subscriber path to be entered (recovery) or a generous timeout.
        try:
            await asyncio.wait_for(subscriber_entered.wait(), timeout=5)
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=5)

    # While Redis was down the fallback must have broadcast at least the
    # health-check failure (mgr.sent may be empty if no DB rows exist, so we
    # only assert the subscriber was entered — that proves recovery happened).
    assert subscriber_entered.is_set(), (
        "RedisFanoutSubscriber.run was never called — supervisor did not recover"
    )
    assert task.done(), "supervisor task must be done after stop"
