"""Unit tests for DetectionHub — FAKE redis + FAKE websockets (no containers).

Assertions:
  (a) first add(cam, ws) → redis subscribe("detections:cam") + relay task started
  (b) a published message → forwarded as {"type":"detection.frame","data":{...}}
  (c) a failing send_json is dropped; remaining sockets still receive
  (d) remove of the last socket → unsubscribe + relay cancelled (ref-count)
"""
from __future__ import annotations

import asyncio
import json

import pytest

from cloud.common.ws.detection_hub import DetectionHub  # noqa: E402

# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakePubSub:
    """Controllable fake that records subscribe/unsubscribe calls."""

    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.subscribed.discard(channel)

    async def aclose(self) -> None:
        self._closed = True

    async def get_message(
        self, *, ignore_subscribe_messages: bool = True, timeout: float = 0.5
    ) -> dict | None:
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return msg
        except TimeoutError:
            return None

    def inject(self, data: dict | bytes | str) -> None:
        """Inject a raw redis message into the fake pubsub queue."""
        payload = json.dumps(data).encode() if isinstance(data, dict) else data
        self._queue.put_nowait({"type": "message", "data": payload})


class FakeRedis:
    """Minimal redis fake that vends the same FakePubSub on every pubsub() call."""

    def __init__(self) -> None:
        self._pubsub = FakePubSub()

    def pubsub(self) -> FakePubSub:
        return self._pubsub


class FakeWebSocket:
    """Records send_json calls; can be made to raise on demand."""

    def __init__(self, *, raises: bool = False) -> None:
        self.sent: list[dict] = []
        self._raises = raises

    async def send_json(self, data: dict) -> None:
        if self._raises:
            msg = "send_json failed"
            raise RuntimeError(msg)
        self.sent.append(data)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _drain() -> None:
    """Yield control to let pending coroutines and tasks run."""
    for _ in range(5):
        await asyncio.sleep(0)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_add_subscribes_and_starts_relay() -> None:
    """(a) first add → subscribe on the channel + relay task created."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws = FakeWebSocket()

    await hub.add("cam_01", ws)  # type: ignore[arg-type]
    await _drain()

    assert "detections:cam_01" in redis._pubsub.subscribed
    assert "cam_01" in hub._relay_tasks
    assert not hub._relay_tasks["cam_01"].done()

    await hub.close()


@pytest.mark.asyncio
async def test_second_add_reuses_relay() -> None:
    """A second add for the same camera does NOT create a second relay task."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()

    await hub.add("cam_01", ws1)  # type: ignore[arg-type]
    first_task = hub._relay_tasks["cam_01"]
    await hub.add("cam_01", ws2)  # type: ignore[arg-type]
    await _drain()

    assert hub._relay_tasks["cam_01"] is first_task  # same task reused
    assert len(hub._sockets["cam_01"]) == 2

    await hub.close()


@pytest.mark.asyncio
async def test_message_forwarded_as_flat_envelope() -> None:
    """(b) an injected message is forwarded as {"type":"detection.frame","data":{...}}."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws = FakeWebSocket()

    await hub.add("cam_02", ws)  # type: ignore[arg-type]
    await _drain()  # let relay start

    payload = {"camera_id": "cam_02", "boxes": [{"x": 10}]}
    redis._pubsub.inject(payload)

    # Give the relay coroutine a chance to pick up the message.
    for _ in range(20):
        await asyncio.sleep(0)
        if ws.sent:
            break

    assert len(ws.sent) == 1
    env = ws.sent[0]
    assert env["type"] == "detection.frame"
    assert env["data"] == payload

    await hub.close()


@pytest.mark.asyncio
async def test_failing_send_drops_socket_others_still_receive() -> None:
    """(c) a socket whose send_json raises is dropped; others still receive."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    good = FakeWebSocket()
    bad = FakeWebSocket(raises=True)

    await hub.add("cam_03", good)  # type: ignore[arg-type]
    await hub.add("cam_03", bad)  # type: ignore[arg-type]
    await _drain()

    payload = {"camera_id": "cam_03", "boxes": []}
    redis._pubsub.inject(payload)

    for _ in range(20):
        await asyncio.sleep(0)
        if good.sent:
            break

    assert len(good.sent) == 1
    assert good.sent[0]["type"] == "detection.frame"
    # bad socket should have been evicted
    assert bad not in hub._sockets.get("cam_03", set())

    await hub.close()


@pytest.mark.asyncio
async def test_remove_last_unsubscribes_and_cancels_relay() -> None:
    """(d) remove of the last socket → unsubscribe + relay task cancelled."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws = FakeWebSocket()

    await hub.add("cam_04", ws)  # type: ignore[arg-type]
    await _drain()

    assert "detections:cam_04" in redis._pubsub.subscribed
    task = hub._relay_tasks["cam_04"]
    assert not task.done()

    await hub.remove("cam_04", ws)  # type: ignore[arg-type]
    await _drain()

    assert "detections:cam_04" not in redis._pubsub.subscribed
    assert task.done()
    assert "cam_04" not in hub._relay_tasks


@pytest.mark.asyncio
async def test_remove_non_last_keeps_relay_running() -> None:
    """Removing one of two sockets keeps the relay alive."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()

    await hub.add("cam_05", ws1)  # type: ignore[arg-type]
    await hub.add("cam_05", ws2)  # type: ignore[arg-type]
    await _drain()

    task = hub._relay_tasks["cam_05"]

    await hub.remove("cam_05", ws1)  # type: ignore[arg-type]
    await _drain()

    # Channel still subscribed and relay still running
    assert "detections:cam_05" in redis._pubsub.subscribed
    assert not task.done()

    await hub.close()


@pytest.mark.asyncio
async def test_close_cancels_all_relays() -> None:
    """hub.close() cancels all running relay tasks."""
    redis = FakeRedis()
    hub = DetectionHub(redis)  # type: ignore[arg-type]
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()

    await hub.add("cam_06", ws_a)  # type: ignore[arg-type]
    await hub.add("cam_07", ws_b)  # type: ignore[arg-type]
    await _drain()

    t6 = hub._relay_tasks["cam_06"]
    t7 = hub._relay_tasks["cam_07"]

    await hub.close()
    await _drain()

    assert t6.done()
    assert t7.done()
