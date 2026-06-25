from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest

from cloud.common.schemas.anomaly import AnomalyEvent, AnomalyType, Severity
from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection
from edge.vision.engine import (
    StubFrameSource,
    VisionEngine,
    build_anomaly_event,
)
from edge.vision.zone_config import CameraConfig, ZoneConfig

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


def test_build_anomaly_event_contract():
    det = Detection("person", (880, 412, 130, 348), 0.91, no_hardhat=True)
    now = datetime(2026, 6, 22, 10, 15, 3, 412000, tzinfo=UTC)
    ev = build_anomaly_event(CFG, "zone_weld_bay", det, "cam_01:1487", now)
    assert isinstance(ev, AnomalyEvent)
    assert ev.schema_version == "1.0"
    uuid.UUID(ev.event_id)
    assert ev.anomaly_type is AnomalyType.PPE_NO_HARDHAT
    assert ev.rule_id == "PPE_NO_HARDHAT"
    assert ev.occurred_at == now
    assert ev.site_id == "plant-01"
    assert ev.camera_id == "cam_01"
    assert ev.zone_id == "zone_weld_bay"
    assert ev.track_id == "cam_01:1487"
    assert ev.object_class == "person"
    assert ev.severity is Severity.HIGH
    assert ev.confidence == pytest.approx(0.91)
    assert ev.evidence.bbox == [880, 412, 130, 348]
    assert ev.evidence.footage_source == "clip_03"
    assert ev.source == "edge"
    parts = ev.dedup_key.split("|")
    assert parts[0] == "cam_01"
    assert parts[1] == "cam_01:1487"
    assert parts[2] == "PPE_NO_HARDHAT"
    assert parts[3].isdigit()


class _FakeTracker:
    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        return [(i + 1, d) for i, d in enumerate(detections)]


class _FixedDetector:
    def __init__(self, dets: list[Detection]):
        self._dets = dets

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return list(self._dets)


@pytest.mark.asyncio
async def test_engine_emits_one_event_after_debounce_confirms():
    published: list[tuple[str, AnomalyEvent]] = []

    async def publish(key: str, ev: AnomalyEvent) -> None:
        published.append((key, ev))

    viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
    detector = _FixedDetector([viol])
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    source = StubFrameSource([frame] * 12)

    engine = VisionEngine(
        CFG,
        detector=detector,
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(
            DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
        ),
        publish=publish,
        frame_source=source,
    )
    count = await engine.run()
    assert count == 1
    assert len(published) == 1
    key, ev = published[0]
    assert key == "cam_01"
    assert ev.anomaly_type is AnomalyType.PPE_NO_HARDHAT
    assert ev.track_id == "cam_01:1"


@pytest.mark.asyncio
async def test_engine_skips_compliant_person():
    published: list[tuple[str, AnomalyEvent]] = []

    async def publish(key: str, ev: AnomalyEvent) -> None:
        published.append((key, ev))

    ok = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=False)
    engine = VisionEngine(
        CFG,
        detector=_FixedDetector([ok]),
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=publish,
        frame_source=StubFrameSource(
            [np.zeros((720, 1280, 3), dtype=np.uint8)] * 12
        ),
    )
    count = await engine.run()
    assert count == 0
    assert published == []


@pytest.mark.asyncio
async def test_engine_checks_all_required_ppe_zones():
    """Person is in zone_b but NOT zone_a; engine must emit exactly one event for zone_b."""
    published: list[tuple[str, AnomalyEvent]] = []

    async def publish(key: str, ev: AnomalyEvent) -> None:
        published.append((key, ev))

    # zone_a: small top-left box that does NOT contain the person's feet (650, 600)
    # zone_b: right-side box that DOES contain the person's feet (650, 600)
    cfg_two = CameraConfig(
        camera_id="cam_01",
        site_id="plant-01",
        rtsp_url="rtsp://mediamtx:8554/cam_01",
        zones=[
            ZoneConfig(
                zone_id="zone_a",
                kind="required_ppe",
                polygon=[(0, 0), (100, 0), (100, 100), (0, 100)],
            ),
            ZoneConfig(
                zone_id="zone_b",
                kind="required_ppe",
                polygon=[(500, 500), (800, 500), (800, 720), (500, 720)],
            ),
        ],
    )
    # bbox (600, 300, 100, 300) => feet anchor = (650, 600), inside zone_b only
    viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
    engine = VisionEngine(
        cfg_two,
        detector=_FixedDetector([viol]),
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(
            DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
        ),
        publish=publish,
        frame_source=StubFrameSource(
            [np.zeros((720, 1280, 3), dtype=np.uint8)] * 12
        ),
    )
    count = await engine.run()
    assert count == 1
    assert len(published) == 1
    _, ev = published[0]
    assert ev.zone_id == "zone_b"
    assert ev.track_id == "cam_01:1"


@pytest.mark.asyncio
async def test_engine_skips_violation_outside_zone():
    published: list[tuple[str, AnomalyEvent]] = []

    async def publish(key: str, ev: AnomalyEvent) -> None:
        published.append((key, ev))

    cfg = CameraConfig(
        camera_id="cam_01",
        site_id="plant-01",
        rtsp_url="rtsp://mediamtx:8554/cam_01",
        zones=[
            ZoneConfig(
                zone_id="zone_weld_bay",
                kind="required_ppe",
                polygon=[(0, 0), (100, 0), (100, 100), (0, 100)],
            )
        ],
    )
    viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
    engine = VisionEngine(
        cfg,
        detector=_FixedDetector([viol]),
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=publish,
        frame_source=StubFrameSource(
            [np.zeros((720, 1280, 3), dtype=np.uint8)] * 12
        ),
    )
    count = await engine.run()
    assert count == 0


def _engine_with_violation(publish=None):
    """Build a VisionEngine whose detector always returns a no_hardhat violation."""
    if publish is None:
        publish = lambda key, ev: None  # noqa: E731

    viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    return VisionEngine(
        CFG,
        detector=_FixedDetector([viol]),
        tracker=_FakeTracker(),
        debouncer=TrackDebouncer(
            DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
        ),
        publish=publish,
        frame_source=StubFrameSource([frame] * 12),
    )


@pytest.mark.asyncio
async def test_edge_detect_span_per_confirmed_anomaly():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    exporter = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("edge-test", exporter=exporter)

    engine = _engine_with_violation()
    await engine.run(max_frames=12)

    spans = [s for s in exporter.get_finished_spans() if s.name == "edge.detect"]
    assert len(spans) >= 1
    assert spans[0].attributes["camera_id"]
