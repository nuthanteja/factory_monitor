#!/usr/bin/env python3
"""Turn a local video into live dashboard incidents using hard-hat-detect/1.

Runs the REAL edge ``VisionEngine`` (zones -> ByteTrack -> M-of-N debounce -> the
exact ``AnomalyEvent`` schema) over a video file, with the Roboflow hard-hat model
as the detector. Each debounced no-hardhat violation is published to Kafka
(``vision.anomalies.v1``), so the already-running cloud pipeline
(ingest_worker -> incident -> escalation -> ``/ws/live``) turns your footage into
live incidents on the dashboard. No mediamtx / RTSP / edge container needed.

Prereqs
-------
* The compose stack is up with Kafka reachable on the host (``localhost:9092``),
  topics created and migrations applied (README quickstart steps 2-5), plus
  ``api`` + ``ingest_worker`` running.
* A dedicated CV venv that has the vision+edge runtime AND the Roboflow package::

      py -3.11 -m venv .venv-cv
      .\\.venv-cv\\Scripts\\python.exe -m pip install inference supervision opencv-python
      .\\.venv-cv\\Scripts\\python.exe -m pip install aiokafka shapely pyyaml \\
          pydantic-settings prometheus-client sqlalchemy \\
          opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

* ``ROBOFLOW_API_KEY`` set in the environment (rotate it if ever pasted anywhere).

Run
---
    $env:ROBOFLOW_API_KEY = "your-rotated-key"
    # run from the repo's app dir so `cloud` and `edge` import from source:
    .\\.venv-cv\\Scripts\\python.exe -m edge.tools.score_video "C:\\path\\workers.mp4"

The whole frame is treated as a required-PPE zone by default, so any tracked bare
head fires. One violation is enough — once an incident is created the cloud
escalates it on its own; watch it appear and count down on the Incidents tab.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

MODEL_ID = "hard-hat-detect/1"
TOPIC = "vision.anomalies.v1"


def _first_frame_size(path: str) -> tuple[int, int]:
    import cv2

    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise SystemExit(f"ERROR: cannot open video: {path}")
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"ERROR: cannot read a frame from: {path}")
    h, w = frame.shape[:2]
    return w, h


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Score a video into dashboard incidents.")
    parser.add_argument("video", help="path to a local video file")
    parser.add_argument("--camera-id", default="cam_demo")
    parser.add_argument("--site-id", default="plant-01")
    parser.add_argument("--bootstrap", default="localhost:9092")
    parser.add_argument("--conf", type=float, default=0.4)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--max-frames", type=int, default=None,
                        help="stop after N frames (default: whole clip)")
    args = parser.parse_args()

    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print("ERROR: set ROBOFLOW_API_KEY in your environment.", file=sys.stderr)
        return 2

    # Imports that need the CV/edge runtime (kept out of module top so --help is fast).
    from inference import get_model

    from cloud.common.kafka import make_producer, publish_event
    from cloud.common.schemas.anomaly import AnomalyEvent
    from edge.publisher.run_edge import ByteTrackTracker
    from edge.vision.debounce import DebounceConfig, TrackDebouncer
    from edge.vision.engine import VisionEngine
    from edge.vision.frame_source import RtspFrameSource
    from edge.vision.roboflow_detector import RoboflowPpeDetector
    from edge.vision.zone_config import CameraConfig, ZoneConfig

    w, h = _first_frame_size(args.video)
    print(f"video={args.video}  {w}x{h}  camera_id={args.camera_id}  kafka={args.bootstrap}")

    # Whole frame = required-PPE zone (guarantees any bare head is 'inside').
    cfg = CameraConfig(
        camera_id=args.camera_id,
        site_id=args.site_id,
        rtsp_url=args.video,
        zones=[
            ZoneConfig(
                zone_id="zone_full_frame",
                kind="required_ppe",
                polygon=[(0, 0), (w, 0), (w, h), (0, h)],
            )
        ],
    )

    model = get_model(model_id=MODEL_ID, api_key=api_key)
    detector = RoboflowPpeDetector(model, conf=args.conf, iou=args.iou)

    producer = await make_producer(args.bootstrap)

    published = 0

    async def publish(key: str, ev: AnomalyEvent) -> None:
        nonlocal published
        published += 1
        print(f"  [incident] {ev.anomaly_type.value} camera={ev.camera_id} "
              f"track={ev.track_id} zone={ev.zone_id} at {ev.occurred_at.isoformat()}")
        await publish_event(producer, TOPIC, ev)

    engine = VisionEngine(
        cfg,
        detector=detector,
        tracker=ByteTrackTracker(),
        debouncer=TrackDebouncer(DebounceConfig()),
        publish=publish,
        frame_source=RtspFrameSource(args.video),
    )

    print("scoring... (bare heads must persist ~8 of 12 frames to fire)")
    try:
        await engine.run(max_frames=args.max_frames)
    finally:
        await producer.stop()

    print(f"\ndone: published {published} anomaly event(s) to {TOPIC}.")
    if published == 0:
        print("no violations fired — try a clip with a clearly bare-headed worker, "
              "or lower --conf.")
    else:
        print("open the dashboard (http://localhost:5173) — incidents should be live.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
