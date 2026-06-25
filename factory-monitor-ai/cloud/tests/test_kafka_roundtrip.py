import asyncio
import json
from pathlib import Path

import pytest

from cloud.common.kafka import (
    deserialize_event,
    make_consumer,
    make_producer,
    publish_event,
    serialize_event,
)
from cloud.common.schemas.anomaly import AnomalyEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "shared" / "contracts" / "anomaly_event.example.json"
TOPIC = "vision.anomalies.v1"


def _event() -> AnomalyEvent:
    return AnomalyEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_serialize_round_trip_pure() -> None:
    ev = _event()
    back = deserialize_event(serialize_event(ev))
    assert back == ev


@pytest.fixture(scope="module")
def kafka_bootstrap():
    from testcontainers.kafka import KafkaContainer

    # testcontainers' KafkaContainer needs Confluent cp-kafka
    # (apache/kafka lacks /etc/confluent/docker/configure)
    with KafkaContainer("confluentinc/cp-kafka:7.6.0") as kafka:
        yield kafka.get_bootstrap_server()


@pytest.mark.asyncio
async def test_publish_then_consume(kafka_bootstrap):
    ev = _event()

    producer = await make_producer(bootstrap=kafka_bootstrap)
    try:
        await publish_event(producer, TOPIC, ev)
        await producer.flush()
    finally:
        await producer.stop()

    consumer = await make_consumer(
        TOPIC, group_id="test-roundtrip", bootstrap=kafka_bootstrap,
        auto_offset_reset="earliest",
    )
    try:
        msg = await asyncio.wait_for(consumer.getone(), timeout=30)
    finally:
        await consumer.stop()

    assert msg.key == ev.camera_id.encode("utf-8")
    assert deserialize_event(msg.value) == ev


@pytest.mark.asyncio
async def test_publish_event_writes_traceparent_header(kafka_bootstrap):
    import asyncio as _asyncio
    import uuid as _uuid

    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    reset_telemetry()
    setup_telemetry("test-producer", exporter=InMemorySpanExporter())

    topic = f"trace-hdr-{_uuid.uuid4().hex[:8]}"
    producer = await make_producer(bootstrap=kafka_bootstrap)
    consumer = await make_consumer(
        topic, group_id=f"g-{_uuid.uuid4().hex[:6]}", bootstrap=kafka_bootstrap,
        auto_offset_reset="earliest",
    )
    try:
        event = _event()
        tracer = trace.get_tracer("t")
        with tracer.start_as_current_span("edge.detect") as span:
            expected_tid = format(span.get_span_context().trace_id, "032x")
            await publish_event(producer, topic, event)
        msg = await _asyncio.wait_for(consumer.getone(), timeout=30)
        # locate the traceparent value (key may be str or bytes depending on aiokafka version)
        tp = next(v for k, v in msg.headers if k == "traceparent")
        assert expected_tid in tp.decode("utf-8")
        assert deserialize_event(msg.value).camera_id == event.camera_id
    finally:
        await producer.stop()
        await consumer.stop()
