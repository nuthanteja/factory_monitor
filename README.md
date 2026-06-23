# Factory Monitor AI

An edge + cloud, event-driven monitoring system for a manufacturing floor: a local
computer-vision engine watches camera feeds for safety/operational anomalies (e.g. a
worker in a required-PPE zone without a hard hat), streams them through Kafka, turns
them into durable incidents, and surfaces them on a real-time command-center dashboard.

> **Status:** Phase 0 + Phase 1 complete — the full vertical slice works end-to-end
> (edge vision → Kafka → ingest → PostgreSQL → API → dashboard), with 85 passing tests
> (cloud + edge integration tests on real containers, plus the frontend suite).
> Escalation (WhatsApp), live WebSocket timers, and platform hardening are the next phases.

## Architecture

```
EDGE (GPU box)                         CLOUD
──────────────                         ─────
RTSP camera → MediaMTX                  ingest worker ── PostgreSQL (source of truth)
   │                                       │              incidents · audit · timers
   └─ Vision Engine                        │
        YOLOv8 + ByteTrack          ┌──► Kafka ──► ingest (dedup + idempotency, DLQ,
        + zone rules + debounce ────┘   vision.        commit-DB-then-offset)
        → AnomalyEvent                  anomalies.v1        │
                                                            ▼
                                        FastAPI read API ──► React dashboard (2s poll)
```

## Tech

- **Edge:** Python, Ultralytics YOLOv8, ByteTrack (supervision), OpenCV, MediaMTX (RTSP/WebRTC).
- **Cloud:** Python, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, aiokafka; PostgreSQL 16, Apache Kafka (KRaft), Redis.
- **Frontend:** React 18, Vite, TypeScript, TanStack Query.
- **Infra:** Docker Compose (one-command local stack), pytest + testcontainers, Vitest + MSW.

## The app lives in [`factory-monitor-ai/`](factory-monitor-ai/)

See [`factory-monitor-ai/README.md`](factory-monitor-ai/README.md) for the quickstart
(the exact `docker compose` start order, migrations, and how to run the edge engine).

## Notes

- The bundled camera clip is a **synthetic placeholder**. For the live CV demo, drop a
  real clip with people/PPE at `factory-monitor-ai/footage/raw/source.mp4` and run the
  footage prep script (see the app README). The automated tests stub the detector and
  need no footage.
- Built collaboratively with AI pair-programming (commits carry `Co-Authored-By` trailers).
