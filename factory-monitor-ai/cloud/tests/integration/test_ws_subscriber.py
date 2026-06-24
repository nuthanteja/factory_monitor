"""End-to-end (Redis + Postgres up): a published compact change is fanned out
by the subscriber as the correct §5.5 WsType broadcast from a fresh DB read.

Uses cloud.common.ws.subscriber.RedisFanoutSubscriber which calls:
    cloud.common.ws.broadcaster.broadcast_change(session_maker, manager, change)
    → manager.broadcast(WsType, data)   ← the 2-arg signature (NOT envelope dict)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.ws.contract import WsType
from cloud.common.ws.subscriber import RedisFanoutSubscriber
from cloud.common.ws_events import CHANGE_CREATED, incident_change
from cloud.common.ws_publisher import publish_incident_event

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")
CHANNEL = "dashboard:incidents:test"


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def redis_container():
    with RedisContainer("redis:7-alpine") as rc:
        yield rc


@pytest.fixture(scope="module")
def migrated_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest.fixture(scope="module")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


class FakeManager:
    """Minimal stand-in for ConnectionManager with the correct 2-arg broadcast."""

    def __init__(self) -> None:
        self.calls: list[tuple[WsType, dict]] = []

    async def broadcast(self, type: WsType, data: dict) -> int:  # noqa: A002
        """Record (type, data) tuples — matches ConnectionManager.broadcast signature."""
        self.calls.append((type, data))
        return 1  # pretend one connection received it


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
        track_id="cam_03:9",
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
async def test_subscriber_broadcasts_published_change(maker, redis_url):
    """A published compact change arrives → subscriber calls manager.broadcast(WsType, data)."""
    inc = await _insert(maker)
    pub = aioredis.from_url(redis_url, decode_responses=False)
    sub = aioredis.from_url(redis_url, decode_responses=False)
    mgr = FakeManager()
    subscriber = RedisFanoutSubscriber(sub, maker, mgr, channel=CHANNEL)

    stop = asyncio.Event()
    task = asyncio.create_task(subscriber.run(stop_event=stop))
    try:
        # Wait until the subscriber has actually called SUBSCRIBE before publishing.
        for _ in range(50):
            if subscriber.subscribed:
                break
            await asyncio.sleep(0.05)
        assert subscriber.subscribed, "subscriber did not become subscribed in time"

        ok = await publish_incident_event(
            pub, CHANNEL, incident_change(CHANGE_CREATED, inc.id)
        )
        assert ok is True

        for _ in range(50):
            if mgr.calls:
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        await pub.aclose()
        await sub.aclose()

    assert len(mgr.calls) == 1
    ws_type, data = mgr.calls[0]
    assert ws_type == WsType.INCIDENT_CREATED
    assert data["incident_id"] == str(inc.id)


@pytest.mark.asyncio
async def test_malformed_message_logged_and_loop_continues(maker, redis_url):
    """A bad payload is swallowed; a subsequent good message still broadcasts."""
    inc = await _insert(maker)
    pub = aioredis.from_url(redis_url, decode_responses=False)
    sub = aioredis.from_url(redis_url, decode_responses=False)
    mgr = FakeManager()
    subscriber = RedisFanoutSubscriber(sub, maker, mgr, channel=CHANNEL + ":malform")

    # handle_raw must return False for malformed input and not raise.
    result = await subscriber.handle_raw(b"not-json{{{")
    assert result is False
    assert mgr.calls == []

    stop = asyncio.Event()
    task = asyncio.create_task(subscriber.run(stop_event=stop))
    try:
        for _ in range(50):
            if subscriber.subscribed:
                break
            await asyncio.sleep(0.05)
        assert subscriber.subscribed

        # Bad message first.
        await pub.publish(CHANNEL + ":malform", b"{{broken")
        await asyncio.sleep(0.1)

        # Good message after the bad one — loop must still be running.
        ok = await publish_incident_event(
            pub, CHANNEL + ":malform", incident_change(CHANGE_CREATED, inc.id)
        )
        assert ok is True

        for _ in range(50):
            if mgr.calls:
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        await pub.aclose()
        await sub.aclose()

    assert len(mgr.calls) == 1
    ws_type, data = mgr.calls[0]
    assert ws_type == WsType.INCIDENT_CREATED
    assert data["incident_id"] == str(inc.id)


@pytest.mark.asyncio
async def test_cancellation_cleans_up(redis_url):
    """Cancelling the run() task unsubscribes and closes the pubsub cleanly."""
    sub = aioredis.from_url(redis_url, decode_responses=False)
    mgr = FakeManager()
    subscriber = RedisFanoutSubscriber(sub, None, mgr, channel=CHANNEL + ":cancel")  # type: ignore[arg-type]

    task = asyncio.create_task(subscriber.run())
    try:
        for _ in range(50):
            if subscriber.subscribed:
                break
            await asyncio.sleep(0.05)
        assert subscriber.subscribed
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass

    # After cancellation the subscriber must report not subscribed and not raise.
    assert not subscriber.subscribed
    await sub.aclose()
