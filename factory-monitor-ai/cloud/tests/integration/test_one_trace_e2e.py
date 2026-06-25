"""End-to-end: one anomaly produces ONE trace across edge → ingest → escalation → notifier.

Uses an in-memory span exporter (no Tempo) so the trace-context propagation through
Kafka headers is asserted in CI, not just visible in a UI.

Binding assertion:
  - All four span names are present in the collected spans.
  - ``ingest.consume`` shares ``edge.detect``'s trace_id AND its parent span_id
    matches ``edge.detect``'s span_id (cross-Kafka W3C traceparent linkage).
  - ``escalation.transition`` and ``notifier.send`` each carry an ``incident_id``
    attribute. They run in separate asyncio calls triggered by the DB timer/outbox,
    so they start new root spans — full DB-hop trace propagation (storing traceparent
    on the incident row) is a documented follow-up for Phase 3b.2.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC
from pathlib import Path

import pytest
from aiokafka import AIOKafkaProducer
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident
from cloud.common.kafka import publish_event
from cloud.common.on_call_resolver import resolve as _resolve_on_call
from cloud.common.schemas.anomaly import AnomalyEvent, AnomalyType, Evidence, Severity
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.escalation_worker.worker import EscalationWorker
from cloud.ingest_worker.consumer import IngestConsumer
from cloud.notifications.chain import ProviderChain
from cloud.notifications.console import ConsoleProvider
from cloud.notifier_worker.relay import run_once as relay_run_once

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")
TOPIC = "vision.anomalies.v1"
DLQ_TOPIC = "vision.anomalies.dlq"


def _async_url(sync_url: str) -> str:
    return (
        sync_url
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _make_anomaly_event() -> AnomalyEvent:
    dedup_key = f"e2e-trace|cam_trace|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}"
    return AnomalyEvent(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type=AnomalyType.PPE_NO_HARDHAT,
        rule_id="PPE_NO_HARDHAT",
        occurred_at=__import__("datetime").datetime.now(UTC),
        site_id="plant-01",
        camera_id="cam_trace",
        zone_id="zone_weld_bay",
        track_id="cam_trace:1",
        object_class="person",
        severity=Severity.HIGH,
        confidence=0.91,
        dedup_key=dedup_key,
        evidence=Evidence(bbox=[100, 100, 50, 100], snapshot_url="", footage_source=""),
        source="edge",
    )


async def _make_incident_due(
    session_maker: async_sessionmaker,
    incident_id: uuid.UUID,
) -> None:
    async with session_maker() as session:
        await session.execute(
            update(Incident)
            .where(Incident.id == incident_id)
            .values(
                next_fire_at=text("now() - interval '1 second'"),
                claimed_until=None,
                claimed_by=None,
            )
        )
        await session.commit()


# ── Module-scoped containers + migrations ────────────────────────────────────

@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def kafka_container():
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


# ── Function-scoped engine + seeded session_maker ────────────────────────────

@pytest.fixture
async def session_maker(migrated_url: str) -> async_sessionmaker:  # type: ignore[return]
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await seed_demo_roster(maker)
    await seed_demo_tiers(maker, site_id="plant-01", delay_seconds=5)
    yield maker
    await engine.dispose()


# ── The headline test ─────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_anomaly_is_one_trace(
    session_maker: async_sessionmaker,
    migrated_url: str,
    kafka_container: KafkaContainer,
) -> None:
    """One anomaly produces one trace across edge → ingest (Kafka hop) + all 4 spans present.

    The cross-Kafka W3C traceparent linkage is the load-bearing assertion:
      ingest.consume.trace_id  == edge.detect.trace_id
      ingest.consume.parent_id == edge.detect.span_id

    escalation.transition and notifier.send are asserted to EXIST with an
    incident_id attribute; they start their own root spans (no DB-hop propagation
    yet — that is Phase 3b.2).
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.config import Settings
    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    # ── 1. Install a fresh in-memory exporter ────────────────────────────────
    exporter = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("e2e", exporter=exporter)

    bootstrap = kafka_container.get_bootstrap_server()

    # ── 2. Produce: wrap publish_event in edge.detect span ───────────────────
    tracer = trace.get_tracer("t")
    edge_producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await edge_producer.start()
    try:
        sample_event = _make_anomaly_event()
        with tracer.start_as_current_span("edge.detect") as root:
            root_tid = root.get_span_context().trace_id
            root_sid = root.get_span_context().span_id
            await publish_event(edge_producer, TOPIC, sample_event)
    finally:
        await edge_producer.stop()

    # ── 3. Consume: drive IngestConsumer until ingest.consume span appears ───
    sync_url = migrated_url.replace("postgresql+asyncpg://", "postgresql://")
    settings = Settings(
        database_url=migrated_url,
        alembic_database_url=sync_url,
        kafka_bootstrap_servers=bootstrap,
        kafka_anomalies_topic=TOPIC,
        kafka_dlq_topic=DLQ_TOPIC,
        kafka_consumer_group=f"trace-e2e-{uuid.uuid4().hex[:8]}",
        ws_fanout_enabled=False,
        operator_grace_seconds=5,
    )

    async def _resolver(session, role, site_id, zone_id, at):
        return await _resolve_on_call(
            session, role=role, site_id=site_id, zone_id=zone_id, at=at
        )

    # Monkey-patch the on_call_resolver used inside IngestConsumer.run_forever
    # so the tier-0 outbox row is enqueued (required for notifier.send span).
    import cloud.ingest_worker.consumer as _consumer_mod
    _orig_resolve = _consumer_mod._resolve_on_call  # noqa: SLF001
    _consumer_mod._resolve_on_call = _resolver  # noqa: SLF001

    ingest = IngestConsumer(settings)
    await ingest.start()
    run_task = asyncio.create_task(ingest.run_forever())

    deadline = asyncio.get_event_loop().time() + 30.0
    consume_span_found = False
    incident_id: uuid.UUID | None = None

    while asyncio.get_event_loop().time() < deadline:
        consume_spans = [
            s for s in exporter.get_finished_spans() if s.name == "ingest.consume"
        ]
        if consume_spans:
            consume_span_found = True
            break
        await asyncio.sleep(0.2)

    await ingest.stop()
    try:
        await asyncio.wait_for(run_task, timeout=5.0)
    except (asyncio.CancelledError, TimeoutError):
        run_task.cancel()

    # Restore original resolver
    _consumer_mod._resolve_on_call = _orig_resolve  # noqa: SLF001

    assert consume_span_found, "ingest.consume span not emitted within 30 s"

    # ── 4. Look up the newly created incident ────────────────────────────────
    async with session_maker() as s:
        row = (
            await s.execute(
                select(Incident).where(Incident.dedup_key == sample_event.dedup_key)
            )
        ).scalar_one_or_none()
    assert row is not None, "incident was not created by ingest.consume"
    incident_id = row.id

    # ── 5. Time-travel → escalation.transition span ──────────────────────────
    await _make_incident_due(session_maker, incident_id)
    worker = EscalationWorker(
        session_maker=session_maker,
        worker_id="trace-e2e-worker",
        lease_seconds=30,
        batch_size=10,
    )
    fired = await worker.run_once()
    assert incident_id in fired, (
        f"escalation worker did not fire incident {incident_id}; "
        f"check that next_fire_at was set and the incident is in an active status"
    )

    # ── 6. Drain outbox → notifier.send span ─────────────────────────────────
    provider_chain = ProviderChain([ConsoleProvider()])
    delivered = await relay_run_once(session_maker, provider_chain)
    assert delivered >= 1, (
        "notifier relay processed 0 outbox rows; "
        "no notifier.send span will be emitted"
    )

    # ── 7. Collect all finished spans ────────────────────────────────────────
    spans_list = exporter.get_finished_spans()
    spans = {s.name: s for s in spans_list}

    # ── 8. Assert all four span names are present ────────────────────────────
    for name in ("edge.detect", "ingest.consume", "escalation.transition", "notifier.send"):
        assert name in spans, (
            f"missing span '{name}'; collected spans: {list(spans)}"
        )

    # ── 9. THE KAFKA-HOP PROOF: ingest.consume is a child of edge.detect ─────
    consume_span = spans["ingest.consume"]
    assert consume_span.context.trace_id == root_tid, (
        f"ingest.consume trace_id {consume_span.context.trace_id:#x} "
        f"!= edge.detect trace_id {root_tid:#x}"
    )
    assert consume_span.parent is not None, (
        "ingest.consume span has no parent — W3C traceparent header was not propagated"
    )
    assert consume_span.parent.span_id == root_sid, (
        f"ingest.consume parent span_id {consume_span.parent.span_id:#x} "
        f"!= edge.detect span_id {root_sid:#x}"
    )

    # ── 10. escalation.transition carries incident_id attribute ──────────────
    esc_span = spans["escalation.transition"]
    assert "incident_id" in esc_span.attributes, (
        f"escalation.transition missing 'incident_id' attribute; "
        f"got: {dict(esc_span.attributes)}"
    )
    assert esc_span.attributes["incident_id"] == str(incident_id)

    # ── 11. notifier.send carries incident_id attribute ──────────────────────
    notify_span = spans["notifier.send"]
    assert "incident_id" in notify_span.attributes, (
        f"notifier.send missing 'incident_id' attribute; "
        f"got: {dict(notify_span.attributes)}"
    )
    assert notify_span.attributes["incident_id"] == str(incident_id)
