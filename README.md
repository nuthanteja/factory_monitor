# Factory Monitor AI

[![CI](https://github.com/nuthanteja/factory_monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/nuthanteja/factory_monitor/actions/workflows/ci.yml)

An edge + cloud, event-driven monitoring system for a manufacturing floor: a local
computer-vision engine watches camera feeds for safety/operational anomalies (e.g. a
worker in a required-PPE zone without a hard hat), streams them through Kafka, turns
them into durable incidents, escalates the ones nobody acknowledges (paging the on-call
chain over WhatsApp/SMS), and surfaces them on a real-time command-center dashboard.

> **Status:** Phase 0–2a complete — the full vertical slice (edge vision → Kafka →
> ingest → PostgreSQL → API → dashboard) plus a durable, **exactly-once escalation
> engine**: durable Postgres timers, an Operator → Floor Manager → Plant Director → Critical
> state machine, a transactional outbox, on-call routing, and WhatsApp/SMS notifications
> (with a fail-closed inbound webhook). All suites green in CI — 146 cloud tests on real
> Postgres + Kafka containers, plus the edge and frontend suites. Live WebSocket timers
> and platform hardening are the next phases.

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
