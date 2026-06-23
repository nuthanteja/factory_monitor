# CPU by default; pass --build-arg BASE_IMAGE=nvidia/cuda:12.4.1-runtime-ubuntu22.04
# (and run with `--gpus all`) for GPU. Ultralytics auto-selects CUDA if present.
ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY edge/requirements.txt /app/edge/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/edge/requirements.txt

# Bring in the shared cloud schema (AnomalyEvent) and the edge package.
COPY cloud/common /app/cloud/common
COPY edge /app/edge

ENV PYTHONPATH=/app

RUN python /app/edge/models/download_weights.py || true

CMD ["python", "-m", "edge.publisher.run_edge"]
