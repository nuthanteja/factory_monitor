from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident, Outbox
from cloud.common.on_call_resolver import resolve as on_call_resolve
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.ingest_worker.consumer import IngestConsumer, handle_message

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
async def seeded_session_maker(migrated_url: str):
    """session_maker with demo roster + tier config seeded for resolver tests."""
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await seed_demo_roster(maker)
    await seed_demo_tiers(maker, site_id="plant-01", delay_seconds=5)
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
        msg = await asyncio.wait_for(consumer.getone(), timeout=10.0)
        assert msg.value == bad
    finally:
        await consumer.stop()


@pytest.mark.asyncio
async def test_handle_message_with_resolver_enqueues_tier0_outbox(
    seeded_session_maker, producer
):
    """Consumer path: handle_message with on_call_resolver enqueues tier-0 outbox row atomically."""
    eid = str(uuid.uuid4())
    value = _valid_value(
        event_id=eid,
        dedup_key=f"cons_res|cam_01|PPE_NO_HARDHAT|{eid[:8]}",
        site_id="plant-01",
    )

    status = await handle_message(
        seeded_session_maker,
        producer,
        value,
        b"cam_01",
        dlq_topic=DLQ_TOPIC,
        grace_seconds=120,
        on_call_resolver=on_call_resolve,
    )

    assert status == "created"

    # Verify the tier-0 outbox row was enqueued in the same transaction
    async with seeded_session_maker() as s:
        # Find the incident by dedup_key
        incident = (
            await s.execute(
                select(Incident).where(
                    Incident.dedup_key == f"cons_res|cam_01|PPE_NO_HARDHAT|{eid[:8]}"
                )
            )
        ).scalar_one()

        outbox_rows = (
            await s.execute(
                select(Outbox).where(Outbox.incident_id == incident.id)
            )
        ).scalars().all()

    assert len(outbox_rows) == 1, (
        f"Expected 1 tier-0 outbox row, got {len(outbox_rows)}"
    )
    row = outbox_rows[0]
    assert row.tier == 0
    assert row.status == "PENDING"
    assert row.to_phone_e164 == "+15550000001"  # Demo Operator phone from seed
    assert row.template_name == "operator_alert_v1"
    assert row.idempotency_key == f"{incident.id}|0"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_consume_span_continues_producer_trace(
    kafka_container: KafkaContainer,
    migrated_url: str,
):
    """ingest.consume span must share the producer's trace_id (cross-Kafka linkage)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.config import Settings
    from cloud.common.kafka import publish_event
    from cloud.common.schemas.anomaly import AnomalyEvent
    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    exporter = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("ingest-test", exporter=exporter)

    bootstrap = kafka_container.get_bootstrap_server()
    topic = "vision.anomalies.v1"

    # Build a Settings that points to the testcontainers
    sync_url = migrated_url.replace("postgresql+asyncpg://", "postgresql://")
    settings = Settings(
        database_url=migrated_url,
        alembic_database_url=sync_url,
        kafka_bootstrap_servers=bootstrap,
        kafka_anomalies_topic=topic,
        kafka_dlq_topic=DLQ_TOPIC,
        kafka_consumer_group=f"span-test-{uuid.uuid4()}",
        ws_fanout_enabled=False,
    )

    # Produce one message inside a parent span so traceparent header is injected
    edge_producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await edge_producer.start()
    try:
        tracer = trace.get_tracer("t")
        with tracer.start_as_current_span("edge.detect") as producer_span:
            producer_tid = producer_span.get_span_context().trace_id
            producer_sid = producer_span.get_span_context().span_id
            sample_event = AnomalyEvent.model_validate({
                "schema_version": "1.0",
                "event_id": str(uuid.uuid4()),
                "anomaly_type": "ppe_no_hardhat",
                "rule_id": "PPE_NO_HARDHAT",
                "occurred_at": "2026-06-24T10:15:03.412Z",
                "site_id": "plant-01",
                "camera_id": "cam_01",
                "zone_id": "zone_weld_bay",
                "track_id": "cam_01:9999",
                "object_class": "person",
                "severity": "high",
                "confidence": 0.91,
                "dedup_key": f"span-test|cam_01|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
                "evidence": {
                    "bbox": [880, 412, 130, 348],
                    "snapshot_url": "",
                    "footage_source": "clip_03",
                },
                "source": "edge",
            })
            await publish_event(edge_producer, topic, sample_event)
    finally:
        await edge_producer.stop()

    # Start the IngestConsumer and run it via run_forever (the span lives in the loop)
    ingest = IngestConsumer(settings)
    await ingest.start()
    run_task = asyncio.create_task(ingest.run_forever())

    # Poll until the ingest.consume span appears (or timeout)
    deadline = asyncio.get_event_loop().time() + 30.0
    consume_spans = []
    while asyncio.get_event_loop().time() < deadline:
        consume_spans = [
            s for s in exporter.get_finished_spans() if s.name == "ingest.consume"
        ]
        if consume_spans:
            break
        await asyncio.sleep(0.2)

    # Stop the consumer (sets _running=False; the loop exits at the next iteration guard)
    await ingest.stop()
    try:
        await asyncio.wait_for(run_task, timeout=5.0)
    except (asyncio.CancelledError, TimeoutError):
        run_task.cancel()

    assert consume_spans, "expected an ingest.consume span — none found"
    span = consume_spans[0]
    assert span.context.trace_id == producer_tid, (
        f"ingest.consume trace_id {span.context.trace_id:#x} "
        f"!= producer trace_id {producer_tid:#x}"
    )
    # The ingest.consume span's parent must be the edge.detect span
    assert span.parent is not None, "ingest.consume span has no parent"
    assert span.parent.span_id == producer_sid, (
        f"ingest.consume parent span_id {span.parent.span_id:#x} "
        f"!= producer span_id {producer_sid:#x}"
    )
