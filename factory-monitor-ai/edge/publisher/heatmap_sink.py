"""Fire-and-forget Kafka sink for per-zone density heatmap payloads."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol


class HeatmapSink(Protocol):
    def publish(self, camera_id: str, payload: dict[str, Any]) -> None: ...


class KafkaHeatmapSink:
    """Bounded-queue, fire-and-forget heatmap publisher.

    ``publish`` is synchronous and non-blocking: it enqueues the payload or
    drops it silently when the queue is full (newest-drop policy).  A
    background drain task sends items to Kafka; any Kafka errors are swallowed
    so they never surface to the detection loop.
    """

    def __init__(self, producer: Any, topic: str, *, maxq: int = 200) -> None:
        self._producer = producer
        self._topic = topic
        self._q: asyncio.Queue[tuple[bytes, bytes]] = asyncio.Queue(maxsize=maxq)
        self._task = asyncio.ensure_future(self._drain())

    def publish(self, camera_id: str, payload: dict[str, Any]) -> None:
        try:
            self._q.put_nowait((camera_id.encode(), json.dumps(payload).encode()))
        except asyncio.QueueFull:
            pass  # drop newest; next tick supersedes

    async def _drain(self) -> None:
        while True:
            key, value = await self._q.get()
            try:
                await self._producer.send_and_wait(self._topic, key=key, value=value)
            except Exception:
                pass  # best-effort; never surface to the detect loop

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
