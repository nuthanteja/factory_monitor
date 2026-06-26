"""HeatmapHub — single global Redis pub/sub relay for dashboard:heatmap.

One pubsub on `dashboard:heatmap`; first WebSocket add starts the relay task,
last remove unsubscribes + cancels.  Each tick is forwarded as
{"type": "heatmap.tick", "data": <parsed payload>} to every connected socket.
Slow/dead sockets are dropped rather than blocking the relay.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class HeatmapHub:
    def __init__(self, redis_client: object, *, channel: str = "dashboard:heatmap") -> None:
        self._redis = redis_client
        self._channel = channel
        self._sockets: set[WebSocket] = set()
        self._relay_task: asyncio.Task | None = None
        self._pubsub: object | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(self, ws: WebSocket) -> None:
        """Register a WebSocket.  Starts the relay on the first add."""
        self._sockets.add(ws)
        if self._relay_task is None or self._relay_task.done():
            await self._start_relay()

    async def remove(self, ws: WebSocket) -> None:
        """Deregister a WebSocket.  Stops the relay when the set empties."""
        self._sockets.discard(ws)
        if not self._sockets:
            await self._stop_relay()

    async def close(self) -> None:
        """Unconditional shutdown — cancel relay and aclose pubsub."""
        await self._stop_relay()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start_relay(self) -> None:
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._relay_task = asyncio.create_task(self._relay_loop(), name="heatmap_relay")
        logger.info("heatmap hub relay started channel=%s", self._channel)

    async def _stop_relay(self) -> None:
        if self._relay_task is not None and not self._relay_task.done():
            self._relay_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._relay_task), timeout=3)
            except (asyncio.CancelledError, TimeoutError):
                pass
            self._relay_task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._pubsub = None
        logger.info("heatmap hub relay stopped")

    async def _relay_loop(self) -> None:
        try:
            while True:
                message = await self._pubsub.get_message(  # type: ignore[union-attr]
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if message is None or message.get("type") != "message":
                    continue
                raw = message["data"]
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("heatmap hub dropped malformed message")
                    continue
                envelope = {"type": "heatmap.tick", "data": data}
                envelope_str = json.dumps(envelope)
                dead: list[WebSocket] = []
                for ws in list(self._sockets):
                    try:
                        await ws.send_text(envelope_str)
                    except Exception:  # noqa: BLE001
                        dead.append(ws)
                for ws in dead:
                    self._sockets.discard(ws)
                    logger.debug("heatmap hub dropped dead socket")
        except asyncio.CancelledError:
            logger.info("heatmap relay loop cancelled")
            raise
        except Exception:
            logger.exception("heatmap relay loop crashed")
            raise
