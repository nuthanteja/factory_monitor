"""Per-camera detection relay hub (design §4a detection overlay).

Manages a lazy-subscribe, ref-counted Redis pub/sub relay:
- First socket for a camera: subscribe("detections:{camera_id}") + start relay task.
- Additional sockets: just added to the set (same relay serves all).
- Last socket removed: unsubscribe + cancel relay task.
- Relay: get_message loop → json.loads → send flat envelope to each socket,
  dropping any socket whose send_json raises (latest-wins; never buffered).

Wire envelope: {"type": "detection.frame", "data": <parsed payload>}

SEPARATE from /ws/live — uses a flat envelope, NOT the sequenced WsEnvelope.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_RELAY_TIMEOUT = 0.5  # seconds; passed to get_message(timeout=...)


class DetectionHub:
    """Ref-counted per-camera relay from Redis pub/sub to WebSocket clients."""

    def __init__(self, redis_client: object) -> None:
        self._redis = redis_client
        self._pubsub: object | None = None  # single shared pubsub handle
        self._sockets: dict[str, set[object]] = {}    # camera_id → set of ws
        self._relay_tasks: dict[str, asyncio.Task[None]] = {}  # camera_id → task

    def _get_pubsub(self) -> object:
        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()  # type: ignore[attr-defined]
        return self._pubsub

    async def add(self, camera_id: str, ws: object) -> None:
        """Add a WebSocket to the hub for camera_id; start relay on first subscriber."""
        if camera_id not in self._sockets:
            self._sockets[camera_id] = set()

        self._sockets[camera_id].add(ws)

        if camera_id not in self._relay_tasks:
            pubsub = self._get_pubsub()
            await pubsub.subscribe(f"detections:{camera_id}")  # type: ignore[attr-defined]
            task = asyncio.create_task(self._relay(camera_id))
            self._relay_tasks[camera_id] = task
            logger.debug("detection hub: relay started camera=%s", camera_id)

    async def remove(self, camera_id: str, ws: object) -> None:
        """Remove a WebSocket; unsubscribe + cancel relay when last subscriber leaves."""
        sockets = self._sockets.get(camera_id)
        if sockets is not None:
            sockets.discard(ws)

        if not self._sockets.get(camera_id):
            # Last subscriber gone — tear down the relay.
            task = self._relay_tasks.pop(camera_id, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task), timeout=2.0
                    )
                except (asyncio.CancelledError, TimeoutError):
                    pass

            pubsub = self._pubsub
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(f"detections:{camera_id}")  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "detection hub: unsubscribe error camera=%s", camera_id
                    )

            self._sockets.pop(camera_id, None)
            logger.debug("detection hub: relay stopped camera=%s", camera_id)

    async def _relay(self, camera_id: str) -> None:
        """Read messages from pub/sub and fan them out to all sockets for camera_id."""
        pubsub = self._get_pubsub()
        channel = f"detections:{camera_id}"
        try:
            while True:
                message = await pubsub.get_message(  # type: ignore[attr-defined]
                    ignore_subscribe_messages=True, timeout=_RELAY_TIMEOUT
                )
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue

                raw = message["data"]
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "detection hub: malformed payload on %s, skipping", channel
                    )
                    continue

                envelope = {"type": "detection.frame", "data": data}
                sockets = list(self._sockets.get(camera_id, []))
                dead: list[object] = []
                for ws in sockets:
                    try:
                        await ws.send_json(envelope)  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001 — drop slow/dead clients
                        dead.append(ws)
                        logger.debug(
                            "detection hub: dropped dead socket camera=%s", camera_id
                        )
                for ws in dead:
                    self._sockets.get(camera_id, set()).discard(ws)

        except asyncio.CancelledError:
            logger.debug("detection hub: relay cancelled camera=%s", camera_id)
            raise

    async def close(self) -> None:
        """Cancel all relay tasks (call on app shutdown)."""
        tasks = list(self._relay_tasks.items())
        for _camera_id, task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(
                *[t for _, t in tasks], return_exceptions=True
            )
        self._relay_tasks.clear()

        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            self._pubsub = None

        logger.debug("detection hub: closed")
