"""Heatmap worker consumer — vision.heatmap.v1 → density_snapshots + live republish."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import ConsumerStoppedError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.config import Settings
from cloud.common.db.models import DensitySnapshot
from cloud.common.db.session import session_factory
from cloud.common.redis_client import get_redis

logger = logging.getLogger(__name__)


async def handle_heatmap(
    session_maker: async_sessionmaker,
    raw_value: bytes,
    *,
    redis: object,
    channel: str,
) -> None:
    """Parse one heatmap tick, persist to DB, then best-effort republish to Redis.

    A malformed payload (bad JSON / missing keys) is logged and skipped so the
    consumer loop never dies on bad data.
    """
    try:
        payload = json.loads(raw_value)
        ts_raw: str = payload["ts"]
        # Parse ISO-8601 "Z" suffix → aware UTC datetime
        ts: datetime = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(UTC)
        rows = [
            DensitySnapshot(
                camera_id=payload["camera_id"],
                zone_id=cell["zone_id"],
                count=cell["count"],
                ts=ts,
            )
            for cell in payload["cells"]
        ]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("heatmap tick skipped — malformed payload: %s", exc)
        return

    async with session_maker() as session:
        session.add_all(rows)
        await session.commit()

    # Best-effort fan-out AFTER DB commit — never block persistence on Redis.
    try:
        await redis.publish(channel, raw_value)
    except Exception:  # noqa: BLE001
        logger.warning("heatmap redis publish failed", exc_info=True)


class HeatmapConsumer:
    """Consume vision.heatmap.v1, persist density_snapshots, republish to Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session_maker = session_factory(settings)
        self._consumer: AIOKafkaConsumer | None = None
        self._redis: object | None = None
        self._running = False

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            self._settings.kafka_heatmap_topic,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=self._settings.kafka_heatmap_group,
            enable_auto_commit=False,
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._redis = get_redis(self._settings)
        self._running = True
        logger.info(
            "heatmap consumer started topic=%s group=%s",
            self._settings.kafka_heatmap_topic,
            self._settings.kafka_heatmap_group,
        )

    async def stop(self) -> None:
        self._running = False
        if self._consumer is not None:
            await self._consumer.stop()

    async def run_forever(self) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                try:
                    await handle_heatmap(
                        self._session_maker,
                        msg.value,
                        redis=self._redis,
                        channel=self._settings.heatmap_redis_channel,
                    )
                    await self._consumer.commit()
                    logger.debug(
                        "processed offset=%s partition=%s",
                        msg.offset,
                        msg.partition,
                    )
                except Exception:
                    logger.exception(
                        "unexpected error processing heatmap message "
                        "topic=%s partition=%s offset=%s — offset NOT committed",
                        msg.topic,
                        msg.partition,
                        msg.offset,
                    )
                    raise
        except (ConsumerStoppedError, asyncio.CancelledError):
            logger.info("heatmap consumer stopped gracefully")
