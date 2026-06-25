from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
from opentelemetry import trace as _otel_trace

from cloud.common.metrics import (
    cam_last_frame_seconds,
    e2e_detect_to_publish_seconds,
    events_emitted_total,
    frames_in_total,
)
from cloud.common.schemas.anomaly import AnomalyEvent, Evidence
from edge.vision.debounce import (
    DebounceEvent,
    TrackDebouncer,
    point_in_polygon,
)
from edge.vision.detector import Detection
from edge.vision.frame_source import FrameSource, StubFrameSource  # noqa: F401
from edge.vision.zone_config import CameraConfig

RULE_ID = "PPE_NO_HARDHAT"
ANOMALY_TYPE = "ppe_no_hardhat"
SEVERITY = "high"
DEDUP_BUCKET_SECONDS = 30  # time-bucket width for dedup_key

PublishFn = Callable[[str, AnomalyEvent], "Awaitable[None] | None"]


class Tracker(Protocol):
    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        ...


class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]:
        ...


def _bbox_anchor(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    """Use the bottom-center (feet) of the bbox as the zone-membership point."""
    x, y, w, h = bbox
    return (x + w / 2.0, y + h)


def build_anomaly_event(
    cfg: CameraConfig,
    zone_id: str,
    detection: Detection,
    track_id: str,
    now: datetime,
    footage_source: str = "clip_03",
) -> AnomalyEvent:
    bucket = int(now.timestamp()) // DEDUP_BUCKET_SECONDS
    x, y, w, h = detection.bbox
    return AnomalyEvent(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type=ANOMALY_TYPE,
        rule_id=RULE_ID,
        occurred_at=now,
        site_id=cfg.site_id,
        camera_id=cfg.camera_id,
        zone_id=zone_id,
        track_id=track_id,
        object_class=detection.object_class,
        severity=SEVERITY,
        confidence=detection.confidence,
        dedup_key=f"{cfg.camera_id}|{track_id}|{RULE_ID}|{bucket}",
        evidence=Evidence(
            bbox=[x, y, w, h],
            snapshot_url="",
            footage_source=footage_source,
        ),
        source="edge",
    )


class VisionEngine:
    def __init__(
        self,
        cfg: CameraConfig,
        detector: Detector,
        tracker: Tracker,
        debouncer: TrackDebouncer,
        publish: PublishFn,
        *,
        frame_source: FrameSource,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.detector = detector
        self.tracker = tracker
        self.debouncer = debouncer
        self.publish = publish
        self.frame_source = frame_source
        self.clock = clock or (lambda: datetime.now(UTC))
        self.zones = [z for z in cfg.zones if z.kind == "required_ppe"]

    async def _emit(self, key: str, ev: AnomalyEvent) -> None:
        result = self.publish(key, ev)
        if inspect.isawaitable(result):
            await result

    async def run(self, max_frames: int | None = None) -> int:
        published = 0
        for i, frame in enumerate(self.frame_source.frames()):
            if max_frames is not None and i >= max_frames:
                break
            frames_in_total.labels(camera_id=self.cfg.camera_id).inc()
            cam_last_frame_seconds.labels(camera_id=self.cfg.camera_id).set(time.time())
            _t_detect = time.perf_counter()
            detections = self.detector.detect(frame)
            tracked = self.tracker.update(detections)
            for raw_id, det in tracked:
                if det.object_class != "person":
                    continue
                track_id = f"{self.cfg.camera_id}:{raw_id}"
                for zone in self.zones:
                    inside = point_in_polygon(_bbox_anchor(det.bbox), zone.polygon)
                    violating = bool(inside and det.no_hardhat)
                    debounce_key = f"{track_id}:{zone.zone_id}"
                    event: DebounceEvent | None = self.debouncer.observe(
                        debounce_key, RULE_ID, violating
                    )
                    if event is not None and event.transition == "open":
                        anomaly = build_anomaly_event(
                            self.cfg, zone.zone_id, det, track_id, self.clock()
                        )
                        with _otel_trace.get_tracer("factory_monitor.edge").start_as_current_span(
                            "edge.detect",
                            attributes={
                                "camera_id": self.cfg.camera_id,
                                "anomaly_type": anomaly.anomaly_type,
                            },
                        ):
                            await self._emit(self.cfg.camera_id, anomaly)
                            events_emitted_total.labels(
                                type=anomaly.anomaly_type.value, camera_id=self.cfg.camera_id
                            ).inc()
                            e2e_detect_to_publish_seconds.labels(
                                camera_id=self.cfg.camera_id
                            ).observe(time.perf_counter() - _t_detect)
                        published += 1
        return published
