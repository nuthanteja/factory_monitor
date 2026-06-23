# Footage

Phase 1 needs ONE short clip looped over RTSP as camera `cam_01`.

## Option A — supply a URL (Creative Commons / public domain)
```bash
FOOTAGE_URL="https://example.com/cc-clip.mp4" ./download_and_encode.sh
```
Use a CC-BY / CC0 / public-domain clip that shows people, ideally some with and
some without hard hats. Do NOT commit copyrighted footage.

## Option B — placeholder local clip
Drop any short (10-30s, ~720p) clip here:
```
footage/raw/source.mp4
```
then:
```bash
./download_and_encode.sh
```

The script pre-encodes to `footage/clips/clip_03.mp4`
(H.264 **baseline**, **yuv420p**) so MediaMTX streams it with `-c copy`
(no CPU re-encode). The encoded clip and raw source are git-ignored.

`footage_source` in emitted `AnomalyEvent.evidence` is the string `clip_03`.

## No host ffmpeg? (containerized)

If the host has no `ffmpeg`/`ffprobe` installed (e.g. a Windows dev machine),
use a containerised ffmpeg image instead of running `download_and_encode.sh`
directly. The pipeline scripts are correct for a host that has ffmpeg; the steps
below are the synthetic-placeholder path used during development.

**Step 1 — generate a synthetic 15s 720p source clip:**
```bash
mkdir -p factory-monitor-ai/footage/raw
docker run --rm \
  -v "E:/Builds/factory_monitor/factory-monitor-ai/footage:/footage" \
  lscr.io/linuxserver/ffmpeg \
  -f lavfi -i testsrc=duration=15:size=1280x720:rate=15 \
  -pix_fmt yuv420p /footage/raw/source.mp4
```

**Step 2 — pre-encode to clips/clip_03.mp4 (mirrors script flags exactly):**
```bash
mkdir -p factory-monitor-ai/footage/clips
docker run --rm \
  -v "E:/Builds/factory_monitor/factory-monitor-ai/footage:/footage" \
  --entrypoint ffmpeg lscr.io/linuxserver/ffmpeg \
  -y -i /footage/raw/source.mp4 \
  -an -c:v libx264 -profile:v baseline -level 3.1 \
  -pix_fmt yuv420p -preset veryfast -g 30 -movflags +faststart \
  /footage/clips/clip_03.mp4
```

**Step 3 — bring up MediaMTX and probe the stream:**
```bash
cd factory-monitor-ai && docker compose up -d mediamtx
sleep 4
docker run --rm --network factory-monitor-ai_default \
  --entrypoint ffprobe lscr.io/linuxserver/ffmpeg \
  -v error -rtsp_transport tcp -select_streams v:0 \
  -show_entries stream=codec_name,width,height \
  -of default=noprint_wrappers=1 \
  -i rtsp://mediamtx:8554/cam_01
```
Expected output: `codec_name=h264`, `width=1280`, `height=720`.

> **For a real demo:** replace `footage/raw/source.mp4` with a clip that shows
> people on a factory floor — ideally some wearing hard hats and some not — then
> re-run Step 2. The synthetic testsrc clip is a placeholder only.
