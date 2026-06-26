"""Tests for per-zone density heatmap emit (Task 1, Phase 4b)."""
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
# Fixtures / shared helpers
# ---------------------------------------------------------------------------

ZONE_A = ZoneConfig(
    zone_id="zone_a",
    kind="required_ppe",
    polygon=[(0, 0), (600, 0), (600, 720), (0, 720)],
)
ZONE_B = ZoneConfig(
    zone_id="zone_b",
    kind="required_ppe",
    polygon=[(400, 0), (1280, 0), (1280, 720), (400, 720)],
)
ZONE_EMPTY = ZoneConfig(
    zone_id="zone_empty",
    kind="required_ppe",
    polygon=[(0, 0), (10, 0), (10, 10), (0, 10)],
)

CFG_TWO = CameraConfig(
    camera_id="cam_test",
    site_id="plant-01",
    rtsp_url="rtsp://localhost/cam_test",
    zones=[ZONE_A, ZONE_B],
)

CFG_THREE = CameraConfig(
    camera_id="cam_test",
    site_id="plant-01",
    rtsp_url="rtsp://localhost/cam_test",
    zones=[ZONE_A, ZONE_EMPTY],
)


class _FakeSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def publish(self, camera_id: str, payload: dict[str, Any]) -> None:
        self.calls.append((camera_id, payload))


class _FakeTracker:
    def __init__(self, dets: list[Detection]) -> None:
        self._dets = dets

    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        return [(i + 1, d) for i, d in enumerate(self._dets)]


class _FixedDetector:
    def __init__(self, dets: list[Detection]) -> None:
        self._dets = dets

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return list(self._dets)


def _blank_frame() -> np.ndarray:
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def _make_engine(
    cfg: CameraConfig,
    dets: list[Detection],
    sink: _FakeSink | None,
    *,
    emit_heatmap: bool = True,
    mono_times: list[float] | None = None,
    heatmap_min_interval_s: float = 5.0,
) -> VisionEngine:
    """Build a VisionEngine wired to a FakeSink and optionally a controlled clock."""
    it = iter(mono_times or [])

    def _mono() -> float:
        try:
            return next(it)
        except StopIteration:
            return 0.0

    return VisionEngine(
        cfg,
        detector=_FixedDetector(dets),
        tracker=_FakeTracker(dets),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=lambda key, ev: None,
        frame_source=StubFrameSource([_blank_frame()]),
        emit_heatmap=emit_heatmap,
        heatmap_sink=sink,
        heatmap_min_interval_s=heatmap_min_interval_s,
        monotonic=_mono if mono_times is not None else None,
    )


# ---------------------------------------------------------------------------
# (a) Emit shape — every configured zone present, empty zone = count 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_shape_all_zones_present() -> None:
    """Every configured zone is in cells; empty zone gets count=0."""
    # person whose feet anchor is in zone_a (x=300, y=600) only — not in zone_empty (0..10)
    person = Detection("person", (250, 300, 100, 300), 0.9, no_hardhat=False)
    sink = _FakeSink()
    engine = _make_engine(
        CFG_THREE,
        [person],
        sink,
        mono_times=[0.0],  # first frame, 0s → emits
    )
    await engine.run(max_frames=1)

    assert len(sink.calls) == 1
    cam_id, payload = sink.calls[0]
    assert cam_id == "cam_test"
    assert payload["camera_id"] == "cam_test"
    assert "ts" in payload
    assert payload["ts"].endswith("Z")
    zone_ids = {c["zone_id"] for c in payload["cells"]}
    assert zone_ids == {"zone_a", "zone_empty"}
    by_id = {c["zone_id"]: c["count"] for c in payload["cells"]}
    assert by_id["zone_a"] == 1
    assert by_id["zone_empty"] == 0


# ---------------------------------------------------------------------------
# (b) Person in two overlapping zones is counted in BOTH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_person_in_overlapping_zones_counted_in_both() -> None:
    """ZONE_A covers 0..600, ZONE_B covers 400..1280; anchor at x=500 is in both."""
    # bbox (450, 300, 100, 300) → feet anchor = (500, 600) — inside ZONE_A and ZONE_B
    person = Detection("person", (450, 300, 100, 300), 0.9, no_hardhat=False)
    sink = _FakeSink()
    engine = _make_engine(
        CFG_TWO,
        [person],
        sink,
        mono_times=[0.0],
    )
    await engine.run(max_frames=1)

    assert len(sink.calls) == 1
    _, payload = sink.calls[0]
    by_id = {c["zone_id"]: c["count"] for c in payload["cells"]}
    assert by_id["zone_a"] == 1
    assert by_id["zone_b"] == 1


# ---------------------------------------------------------------------------
# (c) ≥5s rate cap — frames within 5s emit exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_cap_frames_within_interval_emit_once() -> None:
    """Three frames at t=0, t=2, t=4 (< 5s apart) → only the first emits."""
    person = Detection("person", (250, 300, 100, 300), 0.9, no_hardhat=False)
    sink = _FakeSink()
    mono_times = [0.0, 2.0, 4.0]
    engine = VisionEngine(
        CFG_TWO,
        detector=_FixedDetector([person]),
        tracker=_FakeTracker([person]),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=lambda key, ev: None,
        frame_source=StubFrameSource([_blank_frame()] * 3),
        emit_heatmap=True,
        heatmap_sink=sink,
        heatmap_min_interval_s=5.0,
        monotonic=iter(mono_times).__next__,
    )
    await engine.run(max_frames=3)
    assert len(sink.calls) == 1


@pytest.mark.asyncio
async def test_rate_cap_emits_again_after_interval() -> None:
    """Frames at t=0 and t=6 (> 5s apart) → both emit."""
    person = Detection("person", (250, 300, 100, 300), 0.9, no_hardhat=False)
    sink = _FakeSink()
    mono_times = [0.0, 6.0]
    engine = VisionEngine(
        CFG_TWO,
        detector=_FixedDetector([person]),
        tracker=_FakeTracker([person]),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=lambda key, ev: None,
        frame_source=StubFrameSource([_blank_frame()] * 2),
        emit_heatmap=True,
        heatmap_sink=sink,
        heatmap_min_interval_s=5.0,
        monotonic=iter(mono_times).__next__,
    )
    await engine.run(max_frames=2)
    assert len(sink.calls) == 2


# ---------------------------------------------------------------------------
# (d) emit_heatmap=False default → sink never called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_off_sink_never_called() -> None:
    """When emit_heatmap is not set (defaults False), sink is never called."""
    person = Detection("person", (250, 300, 100, 300), 0.9, no_hardhat=False)
    sink = _FakeSink()
    engine = VisionEngine(
        CFG_TWO,
        detector=_FixedDetector([person]),
        tracker=_FakeTracker([person]),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=lambda key, ev: None,
        frame_source=StubFrameSource([_blank_frame()] * 3),
        # emit_heatmap not passed — must default to False
        heatmap_sink=sink,
    )
    await engine.run(max_frames=3)
    assert sink.calls == []


# ---------------------------------------------------------------------------
# (e) KafkaHeatmapSink.publish drops on QueueFull
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kafka_sink_drops_on_queue_full() -> None:
    """With maxq=1, a second publish while drain is blocked is silently dropped."""
    from edge.publisher.heatmap_sink import KafkaHeatmapSink

    # A fake producer whose send blocks until released
    barrier = asyncio.Event()
    sent: list[bytes] = []

    class _BlockingProducer:
        async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> None:
            await barrier.wait()
            sent.append(value)

    sink = KafkaHeatmapSink(_BlockingProducer(), "vision.heatmap.v1", maxq=1)
    try:
        # First publish — fills the queue (drain is blocking on barrier)
        sink.publish("cam_01", {"ts": "t1", "cells": []})
        # Give the drain loop a chance to dequeue the first item
        await asyncio.sleep(0)
        # Second publish while drain is still blocked → queue is empty at this
        # point because drain already dequeued — put a second item
        sink.publish("cam_01", {"ts": "t2", "cells": []})
        # Third publish while second item is queued → QueueFull → dropped
        sink.publish("cam_01", {"ts": "t3", "cells": []})

        # Unblock the drain
        barrier.set()
        await asyncio.sleep(0.05)

        # At most 2 items were ever in flight (the two that made it in)
        # The key property: no exception was raised and the process continued
        assert len(sent) <= 2
    finally:
        await sink.close()
