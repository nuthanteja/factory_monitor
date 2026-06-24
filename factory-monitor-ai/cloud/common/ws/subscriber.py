"""Redis pub/sub subscriber — the live (Redis-up) fan-out path (design §3.2).

Subscribes to the WS channel; for each published compact change it calls
broadcast_change, which re-reads the incident from Postgres and broadcasts
a fresh §5.5 envelope via the ConnectionManager.  A malformed payload is
logged and skipped so one bad message never kills the live feed.

The correct broadcaster is cloud.common.ws.broadcaster.broadcast_change:
    broadcast_change(session_maker, manager, change) -> int
which calls manager.broadcast(WsType, data) — the manager owns envelope
framing and per-connection seq assignment.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.ws.broadcaster import broadcast_change
from cloud.common.ws_events import decode_change

logger = logging.getLogger(__name__)


class RedisFanoutSubscriber:
    """Subscribes to a Redis pub/sub channel and fans out changes via manager."""

    def __init__(
        self,
        redis_client: object,
        session_maker: async_sessionmaker,
        manager: object,  # ConnectionManager (or any object with broadcast(WsType, dict)->int)
        *,
        channel: str,
    ) -> None:
        self._redis = redis_client
        self._session_maker = session_maker
        self._manager = manager
        self._channel = channel
        self.subscribed = False

    async def handle_raw(self, raw: str | bytes) -> bool:
        """Decode one pubsub payload and broadcast it.  Never raises."""
        try:
            change = decode_change(raw)
        except Exception:  # noqa: BLE001 — bad payload must not kill the loop
            logger.warning("ws subscriber dropped malformed payload", exc_info=True)
            return False
        try:
            sent = await broadcast_change(self._session_maker, self._manager, change)
            return sent > 0
        except Exception:  # noqa: BLE001 — a broadcast error must not kill the loop
            logger.exception("ws subscriber broadcast failed change=%s", change)
            return False

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Subscribe and dispatch until stop_event is set or the task is cancelled."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self.subscribed = True
        logger.info("ws redis subscriber listening channel=%s", self._channel)
        try:
            while stop_event is None or not stop_event.is_set():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                await self.handle_raw(message["data"])
        except asyncio.CancelledError:
            logger.info("ws redis subscriber cancelled")
            raise
        finally:
            self.subscribed = False
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()
