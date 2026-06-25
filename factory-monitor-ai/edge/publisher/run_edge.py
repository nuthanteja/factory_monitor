"""Edge entrypoint: wire RTSP -> YOLOv8 -> ByteTrack -> debounce -> Kafka."""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from pathlib import Path

from cloud.common.kafka import make_producer, publish_event
from cloud.common.schemas.anomaly import AnomalyEvent
from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection, PpeDetector
from edge.vision.engine import VisionEngine
from edge.vision.frame_source import RtspFrameSource
from edge.vision.zone_config import load_all_camera_configs

TOPIC = "vision.anomalies.v1"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cameras"


class ByteTrackTracker:
    """Adapts supervision.ByteTrack to (id, Detection) tuples."""

    def __init__(self) -> None:
        import supervision as sv

        self._sv = sv
        self._tracker = sv.ByteTrack()

    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        import numpy as np

        sv = self._sv
        if not detections:
            return []
        xyxy = np.array(
            [
                [d.bbox[0], d.bbox[1], d.bbox[0] + d.bbox[2], d.bbox[1] + d.bbox[3]]
                for d in detections
            ],
            dtype=float,
        )
        conf = np.array([d.confidence for d in detections], dtype=float)
        class_id = np.arange(len(detections), dtype=int)
        sv_dets = sv.Detections(xyxy=xyxy, confidence=conf, class_id=class_id)
        tracked = self._tracker.update_with_detections(sv_dets)
        out: list[tuple[int, Detection]] = []
        for i in range(len(tracked)):
            if tracked.tracker_id is None or tracked.tracker_id[i] is None:
                continue
            tid = int(tracked.tracker_id[i])
            orig_idx = int(tracked.class_id[i])
            out.append((tid, detections[orig_idx]))
        return out


async def amain() -> None:
    from cloud.common.config import get_settings as _get_settings
    from cloud.common.logging_json import setup_json_logging
    from cloud.common.telemetry import setup_telemetry

    setup_json_logging()
    _s = _get_settings()
    setup_telemetry(_s.otel_service_name or "edge", endpoint=_s.otel_exporter_otlp_endpoint)

    from cloud.common.metrics import edge_heartbeat_total, start_metrics_server

    start_metrics_server(_s.edge_metrics_port)
    _node = os.environ.get("EDGE_NODE_NAME") or socket.gethostname()

    async def _heartbeat() -> None:
        while True:
            edge_heartbeat_total.labels(node=_node).inc()
            await asyncio.sleep(5)

    _hb_task = asyncio.create_task(_heartbeat())

    cfgs = load_all_camera_configs(CONFIG_DIR)
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")

    from ultralytics import YOLO

    weights = os.environ.get("EDGE_WEIGHTS", "edge/models/yolov8n.pt")
    # ONE shared detector — holds the YOLO weights in memory once for all cameras.
    detector = PpeDetector(YOLO(weights))

    # ONE shared producer — acks="all" + enable_idempotence=True (via make_producer).
    producer = await make_producer(bootstrap)

    async def publish(key: str, ev: AnomalyEvent) -> None:
        await publish_event(producer, TOPIC, ev)

    # Optional detection overlay sink (default-off).
    _det_sink = None
    if _s.emit_detections:
        from cloud.common.redis_client import get_redis
        from edge.publisher.detection_sink import RedisDetectionSink

        _det_sink = RedisDetectionSink(
            get_redis(_s),
            maxq=200,
        )

    # PER-CAMERA tracker, debouncer, and frame source so that track-id spaces
    # cannot collide across cameras (ByteTrack counters are instance-local).
    # NOTE: asyncio.gather runs engines concurrently.  Each engine yields with
    # asyncio.sleep(0) once per frame so the other engines and the heartbeat task
    # can round-robin between frames.  Cameras are still serialized during the
    # blocking cap.read(); true per-camera parallelism (thread/executor pool or
    # batched inference) is deferred to a later phase.
    engines = [
        VisionEngine(
            cfg,
            detector=detector,  # shared
            tracker=ByteTrackTracker(),  # per-camera (own track-id space)
            debouncer=TrackDebouncer(
                DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
            ),
            publish=publish,
            frame_source=RtspFrameSource(cfg.rtsp_url),
            emit_detections=_s.emit_detections,
            detection_sink=_det_sink,
            detection_max_fps=_s.detection_max_fps,
        )
        for cfg in cfgs
    ]
    tasks = [asyncio.create_task(e.run()) for e in engines]
    try:
        await asyncio.gather(*tasks)
    finally:
        _hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _hb_task
        if _det_sink is not None:
            await _det_sink.close()
        await producer.stop()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
