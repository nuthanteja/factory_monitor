from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from alembic import command
from alembic.config import Config

from cloud.common.db.models import Incident
from cloud.ingest_worker.consumer import handle_message

DLQ_TOPIC = "vision.anomalies.dlq"
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
def kafka_container():
    # testcontainers' KafkaContainer needs Confluent cp-kafka
    # (apache/kafka lacks /etc/confluent/docker/configure and exits)
    with KafkaContainer("confluentinc/cp-kafka:7.6.0") as kc:
        yield kc


@pytest.fixture(scope="module")
def migrated_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest_asyncio.fixture
async def session_maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def producer(kafka_container: KafkaContainer):
    p = AIOKafkaProducer(bootstrap_servers=kafka_container.get_bootstrap_server())
    await p.start()
    yield p
    await p.stop()


def _valid_value(**overrides) -> bytes:
    base = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "anomaly_type": "ppe_no_hardhat",
        "rule_id": "PPE_NO_HARDHAT",
        "occurred_at": "2026-06-22T10:15:03.412Z",
        "site_id": "plant-01",
        "camera_id": "cam_01",
        "zone_id": "zone_weld_bay",
        "track_id": "cam_01:1487",
        "object_class": "person",
        "severity": "high",
        "confidence": 0.91,
        "dedup_key": "cam_01|cam_01:1487|PPE_NO_HARDHAT|28051503",
        "evidence": {"bbox": [880, 412, 130, 348], "snapshot_url": "", "footage_source": "clip_03"},
        "source": "edge",
    }
    base.update(overrides)
    return json.dumps(base).encode("utf-8")


@pytest.mark.asyncio
async def test_valid_message_creates_incident(session_maker, producer):
    eid = str(uuid.uuid4())
    value = _valid_value(event_id=eid, dedup_key=f"cam_01|cam_01:1|PPE_NO_HARDHAT|{eid[:8]}")

    status = await handle_message(
        session_maker, producer, value, b"cam_01", dlq_topic=DLQ_TOPIC, grace_seconds=120
    )

    assert status == "created"
    async with session_maker() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(Incident)
                .where(Incident.dedup_key == f"cam_01|cam_01:1|PPE_NO_HARDHAT|{eid[:8]}")
            )
        ).scalar_one()
        assert n == 1


@pytest.mark.asyncio
async def test_malformed_message_goes_to_dlq_and_creates_no_incident(
    session_maker, producer, kafka_container
):
    async with session_maker() as s:
        before = (await s.execute(select(func.count()).select_from(Incident))).scalar_one()

    bad = b'{"this_is": "not", "an": "anomaly_event"'  # invalid JSON

    status = await handle_message(
        session_maker, producer, bad, b"cam_01", dlq_topic=DLQ_TOPIC, grace_seconds=120
    )
    assert status == "dlq"

    async with session_maker() as s:
        after = (await s.execute(select(func.count()).select_from(Incident))).scalar_one()
    assert after == before

    consumer = AIOKafkaConsumer(
        DLQ_TOPIC,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        auto_offset_reset="earliest",
        group_id=f"dlq-check-{uuid.uuid4()}",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        msg = await consumer.getone()
        assert msg.value == bad
    finally:
        await consumer.stop()
