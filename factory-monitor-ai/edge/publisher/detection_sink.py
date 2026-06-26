"""Redis pub/sub sink for per-frame detection boxes.

`RedisDetectionSink` is fire-and-forget: `publish()` is synchronous and
non-blocking (enqueues to a bounded asyncio.Queue and returns immediately).
A background `_drain()` task reads from the queue and calls redis.publish.
Backpressure is handled by dropping the newest message when the queue is full.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DetectionSink(Protocol):
    """Minimal protocol every detection sink must satisfy."""

    def publish(self, camera_id: str, payload: dict[str, Any]) -> None: ...


class RedisDetectionSink:
    """Bounded asyncio.Queue + background drain task → redis pub/sub."""

    def __init__(self, redis: Any, *, maxq: int = 200) -> None:
        self._redis = redis
        self._q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=maxq)
        self._drain_task: asyncio.Task[None] = asyncio.ensure_future(self._drain())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, camera_id: str, payload: dict[str, Any]) -> None:
        """Non-blocking enqueue; silently drop if queue is full."""
        try:
            self._q.put_nowait((f"detections:{camera_id}", json.dumps(payload)))
        except asyncio.QueueFull:
            pass  # drop on backpressure — caller must never block

    async def close(self) -> None:
        """Cancel the drain task and wait for it to finish."""
        self._drain_task.cancel()
        try:
            await self._drain_task
        except (asyncio.CancelledError, Exception):
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _drain(self) -> None:
        while True:
            channel, data = await self._q.get()
            try:
                await self._redis.publish(channel, data)
            except Exception:  # noqa: BLE001
                pass  # fire-and-forget — never surface errors to the caller
