"""Thin aiokafka helpers: typed producer/consumer + AnomalyEvent (de)serialization."""
from __future__ import annotations

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from cloud.common.config import get_settings
from cloud.common.schemas.anomaly import AnomalyEvent


def serialize_event(event: AnomalyEvent) -> bytes:
    """Canonical UTF-8 JSON bytes for the wire."""
    return event.model_dump_json().encode("utf-8")


def deserialize_event(raw: bytes) -> AnomalyEvent:
    return AnomalyEvent.model_validate_json(raw.decode("utf-8"))


def _bootstrap(bootstrap: str | None) -> str:
    return bootstrap or get_settings().kafka_bootstrap_servers


async def make_producer(bootstrap: str | None = None) -> AIOKafkaProducer:
    producer = AIOKafkaProducer(
        bootstrap_servers=_bootstrap(bootstrap),
        acks="all",
        enable_idempotence=True,
    )
    await producer.start()
    return producer


async def make_consumer(
    topic: str,
    group_id: str,
    bootstrap: str | None = None,
    auto_offset_reset: str = "earliest",
    enable_auto_commit: bool = False,
) -> AIOKafkaConsumer:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=_bootstrap(bootstrap),
        group_id=group_id,
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=enable_auto_commit,
    )
    await consumer.start()
    return consumer


async def publish_event(
    producer: AIOKafkaProducer, topic: str, event: AnomalyEvent
) -> None:
    """Produce the event keyed by camera_id (partition affinity per camera)."""
    await producer.send_and_wait(
        topic,
        key=event.camera_id.encode("utf-8"),
        value=serialize_event(event),
    )
