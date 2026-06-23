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
