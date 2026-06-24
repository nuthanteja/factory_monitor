# Factory Monitor AI

Edge + cloud factory-floor anomaly detection (PPE / zone intrusion) with a durable
escalation state machine. Monorepo: `edge/` (YOLOv8 + ByteTrack publisher),
`cloud/` (FastAPI API, Kafka ingest worker, Postgres source of truth), `frontend/`
(React + Vite dashboard).

## Quickstart (start order matters)

`docker compose up` alone does **not** create Kafka topics or run migrations — those
are behind `profiles: ["init"]` or a one-shot `run`. Follow this sequence exactly:

```bash
# 1. Copy env template (skips if .env already exists)
cp -n .env.example .env

# 2. Core infra — wait until healthy
docker compose up -d --wait postgres kafka redis mediamtx

# 3. Create Kafka topics (exits when done)
docker compose --profile init up kafka-init --exit-code-from kafka-init

# 4. Apply DB migrations
docker compose run --rm migrate

# 5. App services
docker compose up -d --build api ingest_worker frontend

# 6. (Optional) Real CV edge demo — requires genuine footage with people;
#    the bundled clip is a synthetic placeholder.
#    Place a real clip at footage/raw/source.mp4, then:
#    bash footage/download_and_encode.sh
#    docker compose --profile edge up -d --build edge

# 7. Open http://localhost:5173 ; API at http://localhost:8000/api/v1/incidents
```

`make up` / `make topics` / `make migrate` are convenience aliases for the same
commands — see `Makefile` for details.

## Legacy Quickstart (Python deps only)

```bash
# Python deps (Python 3.11+)
python -m pip install -e ".[dev]"

# Run the test suite
make test
```

## Layout

| Path | Purpose |
|---|---|
| `cloud/common/` | shared db models, pydantic schemas, config, kafka helpers |
| `cloud/ingest_worker/` | Kafka consumer → creates incidents |
| `cloud/api/` | FastAPI app |
| `cloud/migrations/` | Alembic migrations |
| `edge/` | vision pipeline + kafka publisher |
| `frontend/` | React + Vite dashboard |
| `shared/contracts/` | cross-language fixtures + JSON schema |

## Delivery semantics & known tradeoffs

The escalation engine is **exactly-once**: a `(incident_id, tier)` idempotency
row is inserted `ON CONFLICT DO NOTHING` inside the same transaction that writes
the audit event, outbox row, and updated incident state.  A durable
`next_fire_at` timestamp in Postgres drives the retry timer, so in-flight state
survives worker restarts and there is no in-memory timer to lose.

Notification **delivery is at-least-once**: the notifier relay sends via the
provider chain *before* committing the `SENT` status.  A crash after the
provider network call but before commit (or two concurrent relay replicas racing
on the same outbox row) can produce a duplicate provider send.  This is
mitigated by passing an `idempotency_key` to every provider and by a
`FOR UPDATE` re-lock on the `messages` read-model row that guards inbound-reply
matching.  Exactly-once *send* (a two-phase `SENDING` claim before the network
call) is a documented **Phase-3** item.

## Live updates (WebSocket)

The dashboard receives incident state and countdown deadlines over a single
multiplexed WebSocket at **`/ws/live`**, served in-process by the `api` service
(no separate process). It replaces the 2s REST poll as the live channel; the
TanStack-Query 2s poll stays as the **resync + fallback** path.

**Envelope.** Every server→browser frame is versioned and sequenced:
`{ "type": "<WsType>", "version": 1, "seq": <n>, "server_now": "<ISO8601 UTC>", "data": {...} }`.
On connect the server sends a `snapshot` (`{ incidents: IncidentView[] }`); thereafter
`incident.created` / `incident.updated` (full `IncidentView`), `incident.tier_advanced`
(`{incident_id, current_tier, status, deadline_at}`), `incident.resolved`, a periodic
`timer.snapshot` re-anchor, and a `system.heartbeat` keepalive. The client subscribes
with `{action:'subscribe', topics:[...], last_seq}`; on a forward `seq` gap it calls
`invalidateQueries()` to REST-resync.

**Fan-out (Redis pub/sub primary + Postgres-poll fallback).** Producers (ingest +
escalation transition) `PUBLISH` to the Redis channel `WS_CHANNEL`
(`dashboard:incidents`) inside/after the state-change transaction. The `api` service
subscribes on startup (FastAPI lifespan) and translates each message into the WS
envelope, broadcasting to all connected sockets. Redis is **non-authoritative**: if it
is down, the API degrades to polling Postgres every `WS_POLL_INTERVAL_SECONDS` for
changed incidents — no escalation correctness depends on Redis.

**Server-authoritative timers.** All deadline math is Postgres `now()`. The browser
renders `remaining = deadline_at − (server_now + local_elapsed)` and **never** computes
escalation; on expiry it shows "OVERDUE — awaiting server" and waits for the next
`incident.tier_advanced`. The client corrects browser↔server clock skew with an
EMA-smoothed offset over `performance.now()`, re-anchored on every `server_now`.

Escalation transitions remain **exactly-once** (idempotency row in Postgres); WS
notification delivery is **at-least-once** (see "Delivery semantics" above).

> **Phase-5 deploy note:** the Twilio inbound-webhook URL is reconstructed from
> the FastAPI `Request` object.  Behind a reverse proxy this must honour
> `X-Forwarded-Proto`/`Host` (or a configured public base URL) so the signed
> URL matches what Twilio used — see the TODO comment in
> `cloud/api/twilio_webhook.py`.

## Make targets

`make up` · `make down` · `make logs` · `make topics` · `make migrate` · `make test`
