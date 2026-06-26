"""Unit tests for HeatmapHub — fake Redis pubsub + fake WebSocket sockets."""
from __future__ import annotations

import asyncio
import json

import pytest

from cloud.common.ws.heatmap_hub import HeatmapHub

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

CHANNEL = "dashboard:heatmap"


class _FakeMessage:
    def __init__(self, data: bytes | str, msg_type: str = "message") -> None:
        self._data = data
        self._type = msg_type

    def get(self, key: str, default=None):  # noqa: ANN001
        if key == "type":
            return self._type
        if key == "data":
            return self._data
        return default


class _FakePubsub:
    """Simulates a redis.asyncio PubSub object."""

    def __init__(self) -> None:
        self.subscribed_channels: list[str] = []
        self.unsubscribed_channels: list[str] = []
        self._messages: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed_channels.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed_channels.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def get_message(
        self, *, ignore_subscribe_messages: bool = True, timeout: float = 0.5
    ) -> dict | None:
        try:
            msg = self._messages.get_nowait()
            return msg
        except asyncio.QueueEmpty:
            await asyncio.sleep(0)  # yield so other tasks can run
            return None

    def push(self, data: bytes | str) -> None:
        """Inject a message into the queue (test helper)."""
        self._messages.put_nowait({"type": "message", "data": data})


class _FakeRedis:
    def __init__(self) -> None:
        self._pubsub = _FakePubsub()

    def pubsub(self) -> _FakePubsub:
        return self._pubsub


class _CountingFakeRedis:
    """Like _FakeRedis but counts how many pubsub() calls are made (race detector)."""

    def __init__(self) -> None:
        self.pubsub_call_count = 0
        self._pubsub = _RacyFakePubsub()

    def pubsub(self) -> _RacyFakePubsub:
        self.pubsub_call_count += 1
        return self._pubsub


class _RacyFakePubsub(_FakePubsub):
    """FakePubsub that yields to the event loop during subscribe to open the race window."""

    async def subscribe(self, channel: str) -> None:
        await asyncio.sleep(0)  # yield — lets a concurrent add() slip through without a lock
        self.subscribed_channels.append(channel)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_on_send = False

    async def send_text(self, data: str) -> None:
        if self.fail_on_send:
            raise OSError("socket closed")
        self.sent.append(data)


# ---------------------------------------------------------------------------
# Helper: run relay briefly and pump messages
# ---------------------------------------------------------------------------


async def _run_relay_ticks(hub: HeatmapHub, n: int = 5) -> None:
    """Let the relay task run for a few event-loop iterations."""
    for _ in range(n):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_add_subscribes_and_starts_relay() -> None:
    """Adding the first socket subscribes to the channel and creates a relay task."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws = _FakeWebSocket()

    await hub.add(ws)

    assert CHANNEL in redis._pubsub.subscribed_channels
    assert hub._relay_task is not None
    assert not hub._relay_task.done()

    await hub.close()


@pytest.mark.asyncio
async def test_tick_forwarded_to_all_sockets() -> None:
    """A published tick is forwarded as {'type':'heatmap.tick','data':...} to all sockets."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()

    await hub.add(ws1)
    await hub.add(ws2)

    payload = {
        "camera_id": "cam_01",
        "ts": "2026-06-25T10:00:00Z",
        "cells": [{"zone_id": "z1", "count": 3}],
    }
    redis._pubsub.push(json.dumps(payload).encode())

    await _run_relay_ticks(hub, n=20)

    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1

    env1 = json.loads(ws1.sent[0])
    assert env1["type"] == "heatmap.tick"
    assert env1["data"]["camera_id"] == "cam_01"

    await hub.close()


@pytest.mark.asyncio
async def test_failing_socket_is_dropped() -> None:
    """A socket that raises on send_text is removed from the active set."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws_good = _FakeWebSocket()
    ws_bad = _FakeWebSocket()
    ws_bad.fail_on_send = True

    await hub.add(ws_good)
    await hub.add(ws_bad)

    payload = {"camera_id": "cam_01", "ts": "2026-06-25T10:00:00Z", "cells": []}
    redis._pubsub.push(json.dumps(payload).encode())

    await _run_relay_ticks(hub, n=20)

    # bad socket must have been evicted
    assert ws_bad not in hub._sockets
    # good socket stays
    assert ws_good in hub._sockets

    await hub.close()


@pytest.mark.asyncio
async def test_last_remove_unsubscribes_and_cancels() -> None:
    """Removing the last socket unsubscribes from the channel and cancels the relay."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws = _FakeWebSocket()

    await hub.add(ws)
    task = hub._relay_task

    await hub.remove(ws)

    # relay task must be cancelled/done
    assert task is not None
    assert task.done()
    # pubsub unsubscribed
    assert CHANNEL in redis._pubsub.unsubscribed_channels
    # pubsub closed
    assert redis._pubsub.closed


@pytest.mark.asyncio
async def test_malformed_message_is_skipped() -> None:
    """A non-JSON message must be silently skipped without crashing the relay."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws = _FakeWebSocket()

    await hub.add(ws)
    redis._pubsub.push(b"not-valid-json{{{{")

    # Also push a valid one after the bad one
    payload = {"camera_id": "cam_01", "ts": "2026-06-25T10:00:00Z", "cells": []}
    redis._pubsub.push(json.dumps(payload).encode())

    await _run_relay_ticks(hub, n=30)

    # Only the valid message should have been forwarded
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["type"] == "heatmap.tick"

    await hub.close()


@pytest.mark.asyncio
async def test_close_cancels_relay_even_with_active_sockets() -> None:
    """close() must cancel the relay even if sockets are still connected."""
    redis = _FakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws = _FakeWebSocket()

    await hub.add(ws)
    task = hub._relay_task

    await hub.close()

    assert task is not None
    assert task.done()


@pytest.mark.asyncio
async def test_concurrent_add_starts_relay_exactly_once() -> None:
    """Two concurrent add() calls from an empty hub must start the relay ONCE.

    The _RacyFakePubsub.subscribe() yields inside (await asyncio.sleep(0)) to open
    the race window that the asyncio.Lock must close.  Without the lock the second
    add() would observe _relay_task is None and call _start_relay() a second time,
    resulting in pubsub_call_count == 2 and two relay tasks.
    """
    redis = _CountingFakeRedis()
    hub = HeatmapHub(redis, channel=CHANNEL)
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()

    # Fire both adds concurrently — the race window is inside subscribe().
    await asyncio.gather(hub.add(ws1), hub.add(ws2))

    # pubsub() must have been called exactly once (one subscription, one relay task).
    assert redis.pubsub_call_count == 1, (
        f"Expected 1 pubsub() call but got {redis.pubsub_call_count} — "
        "concurrent add() is starting the relay more than once (add-race)"
    )
    assert hub._relay_task is not None
    assert not hub._relay_task.done()
    # Both sockets must be registered.
    assert ws1 in hub._sockets
    assert ws2 in hub._sockets

    await hub.close()
