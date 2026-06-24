"""End-to-end (Redis up): a published compact change is fanned out by the
subscriber as the correct §5.5 envelope built from a fresh DB read."""
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

from cloud.api.ws.subscriber import RedisFanoutSubscriber
from cloud.common.db.models import Incident, IncidentStatus
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
    def __init__(self) -> None:
        self._seq = 0
        self.sent: list[dict] = []

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def broadcast(self, envelope: dict) -> None:
        self.sent.append(envelope)


async def _insert(maker) -> Incident:
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(), site_id="plant-01", camera_id="cam_03", zone_id="z",
        anomaly_type="ppe_no_hardhat", rule_id="PPE_NO_HARDHAT",
        object_class="person", track_id="cam_03:9", severity="high",
        dedup_key=f"k|{uuid.uuid4().hex}|PPE|b",
        status=IncidentStatus.AWAITING_OPERATOR, current_tier=0,
        next_fire_at=now + timedelta(seconds=120),
        deadline_at=now + timedelta(seconds=120),
        snapshot_url="", is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


@pytest.mark.asyncio
async def test_subscriber_broadcasts_published_change(maker, redis_url):
    inc = await _insert(maker)
    pub = aioredis.from_url(redis_url, decode_responses=False)
    sub = aioredis.from_url(redis_url, decode_responses=False)
    mgr = FakeManager()
    subscriber = RedisFanoutSubscriber(sub, maker, mgr, channel=CHANNEL)

    stop = asyncio.Event()
    task = asyncio.create_task(subscriber.run(stop_event=stop))
    try:
        # Give the subscriber a moment to actually SUBSCRIBE before publishing.
        for _ in range(50):
            if subscriber.subscribed:
                break
            await asyncio.sleep(0.05)
        assert subscriber.subscribed

        ok = await publish_incident_event(
            pub, CHANNEL, incident_change(CHANGE_CREATED, inc.id)
        )
        assert ok is True

        for _ in range(50):
            if mgr.sent:
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        await pub.aclose()
        await sub.aclose()

    assert len(mgr.sent) == 1
    env = mgr.sent[0]
    assert env["type"] == "incident.created"
    assert env["version"] == 1
    assert env["data"]["incident_id"] == str(inc.id)


@pytest.mark.asyncio
async def test_handle_raw_ignores_malformed_payload(maker, redis_url):
    sub = aioredis.from_url(redis_url, decode_responses=False)
    mgr = FakeManager()
    subscriber = RedisFanoutSubscriber(sub, maker, mgr, channel=CHANNEL)
    # Not JSON — must be swallowed, returns False, no broadcast, no raise.
    handled = await subscriber.handle_raw(b"not-json{{{")
    assert handled is False
    assert mgr.sent == []
    await sub.aclose()
