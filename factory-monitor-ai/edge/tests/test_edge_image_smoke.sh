#!/usr/bin/env bash
# Build the edge image and verify the package imports + the engine module loads.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../.. && pwd)"  # repo root (factory-monitor-ai)

echo "[smoke] building edge image"
docker build -f "${HERE}/edge/Dockerfile" -t fm_edge:smoke "${HERE}"

echo "[smoke] importing edge + cloud schema inside the image"
docker run --rm fm_edge:smoke python -c "
from cloud.common.schemas.anomaly import AnomalyEvent
from edge.vision.engine import VisionEngine, build_anomaly_event
from edge.vision.detector import PpeDetector, Detection
from edge.vision.debounce import TrackDebouncer, point_in_polygon
from edge.vision.zone_config import load_camera_config
print('edge-import-ok')
"

echo "[smoke] OK"
