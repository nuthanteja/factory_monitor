#!/usr/bin/env bash
# Verify MediaMTX is serving cam_01 as a readable H.264 RTSP stream.
# Run from the host AFTER `docker compose up -d mediamtx` and after the
# footage clip exists (download_and_encode.sh has been run).
set -euo pipefail
RTSP_URL="${RTSP_URL:-rtsp://localhost:8554/cam_01}"

echo "[verify] probing ${RTSP_URL}"
ffprobe -v error -rtsp_transport tcp \
  -select_streams v:0 \
  -show_entries stream=codec_name,width,height \
  -of default=noprint_wrappers=1 \
  -i "${RTSP_URL}"

echo "[verify] OK: cam_01 RTSP stream is readable"
