"""Tests for per-frame detection emit (gated, rate-capped, fire-and-forget).

Covers:
  (a) payload shape when emit_detections=True
  (b) rate cap — frames faster than detection_max_fps are dropped
  (c) default-off — emit_detections=False never calls the sink
  (d) RedisDetectionSink.publish drops on QueueFull (backpressure safety)
"""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection
from edge.vision.engine import StubFrameSource, VisionEngine
from edge.vision.zone_config import CameraConfig, ZoneConfig

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

CFG = CameraConfig(
    camera_id="cam_01",
    site_id="plant-01",
    rtsp_url="rtsp://mediamtx:8554/cam_01",
    zones=[
        ZoneConfig(
            zone_id="zone_weld_bay",
            kind="required_ppe",
            polygon=[(0, 0), (1280, 0), (1280, 720), (0, 720)],
        )
    ],
)

FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


class _FakeTracker:
    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        return [(i + 1, d) for i, d in enumerate(detections)]


class _FixedDetector:
    def __init__(self, dets: list[Detection]) -> None:
        self._dets = dets

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return list(self._dets)


class FakeSink:
    """Records every publish() call so tests can assert against them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def publish(self, camera_id: str, payload: dict[str, Any]) -> None:
        self.calls.append((camera_id, payload))


def _noop_publish(key: str, ev: Any) -> None:
    pass


def _make_engine(
    dets: list[Detection],
    n_frames: int,
    *,
    emit_detections: bool,
    sink: FakeSink | None = None,
    detection_max_fps: float = 10.0,
    monotonic=None,
) -> VisionEngine:
    kwargs: dict[str, Any] = dict(
        emit_detections=emit_detections,
        detection_sink=sink,
        detection_max_fps=detection_max_fps,
    )
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return VisionEngine(
        CFG,
        detector=_FixedDetector(dets),
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=_noop_publish,
        frame_source=StubFrameSource([FRAME] * n_frames),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# (a) Payload shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_payload_shape():
    """Emitting with one detection per frame → correct field names and types."""
    dets = [Detection("person", (10, 20, 50, 100), 0.9, no_hardhat=True)]
    sink = FakeSink()

    # Use a monotonic clock that advances 1 second per call so every frame is emitted.
    call_count = 0

    def _clock() -> float:
        nonlocal call_count
        call_count += 1
        return float(call_count)

    engine = _make_engine(
        dets,
        n_frames=3,
        emit_detections=True,
        sink=sink,
        detection_max_fps=1.0,  # 1 FPS cap → 1 s gap required
        monotonic=_clock,
    )
    await engine.run()

    assert len(sink.calls) >= 1, "Expected at least one emit"
    cam_id, payload = sink.calls[0]
    assert cam_id == "cam_01"
    assert payload["camera_id"] == "cam_01"
    assert payload["frame_w"] == 1280
    assert payload["frame_h"] == 720
    assert isinstance(payload["seq"], int)
    assert payload["seq"] >= 1
    assert isinstance(payload["ts"], float)
    assert isinstance(payload["boxes"], list)
    assert len(payload["boxes"]) == 1
    box = payload["boxes"][0]
    assert box["cls"] == "person"
    assert box["bbox"] == [10, 20, 50, 100]
    assert box["track_id"] == 1
    assert box["no_hardhat"] is True


# ---------------------------------------------------------------------------
# (b) Rate cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_cap_drops_excess_frames():
    """Frames fed faster than detection_max_fps must be dropped.

    Strategy: inject a monotonic clock that advances by 0.05 s per call
    (20 FPS equivalent).  With detection_max_fps=10.0, only every other
    call crosses the 0.1 s threshold, so out of 10 frames we expect 5 emits.
    """
    dets = [Detection("person", (0, 0, 10, 10), 0.8, no_hardhat=False)]
    sink = FakeSink()

    # Clock advances 0.05 s per call → 20 FPS feed, 10 FPS cap → 50 % pass
    t = [0.0]

    def _clock() -> float:
        t[0] += 0.05
        return t[0]

    engine = _make_engine(
        dets,
        n_frames=10,
        emit_detections=True,
        sink=sink,
        detection_max_fps=10.0,
        monotonic=_clock,
    )
    await engine.run()

    # At 0.05 s steps with 0.1 s required gap: frames at t=0.05,0.15,0.25,0.35,0.45
    # should pass (5 out of 10).  Allow ±1 for boundary rounding.
    assert 4 <= len(sink.calls) <= 6, (
        f"Expected ~5 emits (rate cap 10 FPS over 20-FPS feed), got {len(sink.calls)}"
    )


# ---------------------------------------------------------------------------
# (c) Default-off — sink must never be called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_off_never_calls_sink():
    dets = [Detection("person", (0, 0, 10, 10), 0.8, no_hardhat=True)]
    sink = FakeSink()
    engine = _make_engine(
        dets,
        n_frames=5,
        emit_detections=False,
        sink=sink,
    )
    await engine.run()
    assert sink.calls == [], "Sink must not be called when emit_detections=False"


# ---------------------------------------------------------------------------
# (d) RedisDetectionSink backpressure — QueueFull drops silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_sink_drops_on_queue_full():
    """publish() with maxq=1 should drop the second call silently."""
    from edge.publisher.detection_sink import RedisDetectionSink

    published: list[tuple[str, str]] = []

    # Fake redis whose publish() records the call but we never await it here
    class _FakeRedis:
        async def publish(self, channel: str, data: str) -> None:
            published.append((channel, data))

    sink = RedisDetectionSink(_FakeRedis(), maxq=1)
    payload = {"camera_id": "cam_01", "boxes": []}

    # Fill queue (maxq=1) then publish again — second should be silently dropped
    sink.publish("cam_01", payload)
    sink.publish("cam_01", payload)  # queue full → drop, no exception

    # Drain the queue so the drain task flushes
    await asyncio.sleep(0.05)
    await sink.close()

    # Only one message should have been published to redis
    assert len(published) == 1, f"Expected 1 publish, got {len(published)}"
