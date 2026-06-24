from __future__ import annotations

import asyncio
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import ConsumerStoppedError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.config import Settings
from cloud.common.db.session import session_factory
from cloud.common.on_call_resolver import resolve as _resolve_on_call
from cloud.common.schemas.anomaly import AnomalyEvent
from cloud.ingest_worker.service import OnCallResolverFn, create_incident_from_anomaly

logger = logging.getLogger(__name__)


async def handle_message(
    session_maker: async_sessionmaker,
    producer: AIOKafkaProducer,
    raw_value: bytes,
    raw_key: bytes | None,
    *,
    dlq_topic: str,
    grace_seconds: int,
    on_call_resolver: OnCallResolverFn | None = None,
) -> str:
    """Validate one record, route malformed to DLQ, else create an incident.

    Returns one of: "created", "duplicate_event_id", "duplicate_open_dedup", "dlq".
    DB is committed BEFORE the caller commits the Kafka offset.

    When on_call_resolver is provided the tier-0 OPERATOR outbox row is enqueued
    atomically in the same transaction (§3.2 / §6 design spec).
    """
    try:
        event = AnomalyEvent.model_validate_json(raw_value)
    except (ValidationError, ValueError) as exc:
        logger.warning("malformed anomaly routed to DLQ: %s", exc)
        # At-least-once: a crash between this DLQ send and the caller's offset
        # commit can produce a duplicate DLQ record.  Acceptable — the DLQ is
        # for human triage and idempotency is not required there.
        await producer.send_and_wait(dlq_topic, value=raw_value, key=raw_key)
        return "dlq"

    async with session_maker() as session:
        result = await create_incident_from_anomaly(
            session, event, grace_seconds=grace_seconds,
            on_call_resolver=on_call_resolver,
        )
        if result.created:
            await session.commit()
        else:
            await session.rollback()
    return result.reason


class IngestConsumer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session_maker = session_factory(settings)
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._running = False

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            self._settings.kafka_anomalies_topic,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=self._settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers
        )
        await self._consumer.start()
        await self._producer.start()
        self._running = True
        logger.info(
            "ingest consumer started topic=%s group=%s",
            self._settings.kafka_anomalies_topic,
            self._settings.kafka_consumer_group,
        )

    async def stop(self) -> None:
        self._running = False
        if self._consumer is not None:
            await self._consumer.stop()
        if self._producer is not None:
            await self._producer.stop()

    async def run_forever(self) -> None:
        assert self._consumer is not None and self._producer is not None
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                try:
                    status = await handle_message(
                        self._session_maker,
                        self._producer,
                        msg.value,
                        msg.key,
                        dlq_topic=self._settings.kafka_dlq_topic,
                        grace_seconds=self._settings.operator_grace_seconds,
                        on_call_resolver=_resolve_on_call,
                    )
                    # COMMIT-ORDER CONTRACT: DB transaction committed by handle_message
                    # BEFORE we commit the Kafka offset here.
                    # A crash between them re-delivers the event; Task 10's source_event_id
                    # unique constraint makes re-delivery a no-op.
                    await self._consumer.commit()
                    logger.debug(
                        "processed offset=%s partition=%s status=%s",
                        msg.offset, msg.partition, status,
                    )
                except Exception:
                    logger.exception(
                        "unexpected error processing message topic=%s partition=%s offset=%s"
                        " — offset NOT committed (redelivery guaranteed)",
                        msg.topic, msg.partition, msg.offset,
                    )
                    raise
        except (ConsumerStoppedError, asyncio.CancelledError):
            logger.info("consumer stopped gracefully")
