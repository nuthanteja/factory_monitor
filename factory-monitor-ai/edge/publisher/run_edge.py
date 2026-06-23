"""Edge entrypoint: wire RTSP -> YOLOv8 -> ByteTrack -> debounce -> Kafka."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiokafka import AIOKafkaProducer

from cloud.common.schemas.anomaly import AnomalyEvent
from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection, PpeDetector
from edge.vision.engine import VisionEngine
from edge.vision.frame_source import RtspFrameSource
from edge.vision.zone_config import load_camera_config

TOPIC = "vision.anomalies.v1"
CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "cameras" / "cam_01.yaml"
)


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
    cfg = load_camera_config(CONFIG_PATH)
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")

    from ultralytics import YOLO

    weights = os.environ.get("EDGE_WEIGHTS", "edge/models/yolov8n.pt")
    detector = PpeDetector(YOLO(weights))

    producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await producer.start()

    async def publish(key: str, ev: AnomalyEvent) -> None:
        await producer.send_and_wait(
            TOPIC,
            key=key.encode("utf-8"),
            value=ev.model_dump_json().encode("utf-8"),
        )

    engine = VisionEngine(
        cfg,
        detector=detector,
        tracker=ByteTrackTracker(),
        debouncer=TrackDebouncer(
            DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
        ),
        publish=publish,
        frame_source=RtspFrameSource(cfg.rtsp_url),
    )
    try:
        await engine.run()
    finally:
        await producer.stop()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
