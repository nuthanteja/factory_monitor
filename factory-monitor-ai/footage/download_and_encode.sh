#!/usr/bin/env bash
# Download (or accept a local placeholder) a short clip and pre-encode it to
# H.264 baseline / yuv420p so MediaMTX can `-c copy` it without re-encoding.
#
# Usage:
#   ./download_and_encode.sh
#   FOOTAGE_URL=https://example/clip.mp4 ./download_and_encode.sh
#
# Output: footage/clips/clip_03.mp4  (looped by MediaMTX as rtsp path cam_01)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="${HERE}/raw"
OUT_DIR="${HERE}/clips"
OUT="${OUT_DIR}/clip_03.mp4"
RAW="${RAW_DIR}/source.mp4"
FOOTAGE_URL="${FOOTAGE_URL:-}"

mkdir -p "${RAW_DIR}" "${OUT_DIR}"

if [[ ! -f "${RAW}" ]]; then
  if [[ -n "${FOOTAGE_URL}" ]]; then
    echo "[footage] downloading ${FOOTAGE_URL}"
    if command -v yt-dlp >/dev/null 2>&1 && [[ "${FOOTAGE_URL}" == *youtube.com/* || "${FOOTAGE_URL}" == *youtu.be/* ]]; then
      yt-dlp -f "mp4" -o "${RAW}" "${FOOTAGE_URL}"
    else
      curl -L --fail -o "${RAW}" "${FOOTAGE_URL}"
    fi
  else
    echo "[footage] ERROR: no FOOTAGE_URL set and ${RAW} is missing." >&2
    echo "[footage] Place any short people-with/without-hardhat clip at:" >&2
    echo "[footage]   ${RAW}" >&2
    echo "[footage] (a 10-30s 720p clip is plenty), then re-run." >&2
    exit 2
  fi
fi

echo "[footage] pre-encoding -> ${OUT} (H.264 baseline, yuv420p)"
ffmpeg -y -i "${RAW}" \
  -an \
  -c:v libx264 \
  -profile:v baseline \
  -level 3.1 \
  -pix_fmt yuv420p \
  -preset veryfast \
  -g 30 \
  -movflags +faststart \
  "${OUT}"

echo "[footage] done: ${OUT}"
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,pix_fmt,width,height \
  -of default=noprint_wrappers=1 "${OUT}"
