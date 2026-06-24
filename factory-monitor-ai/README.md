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

## Delivery semantics

Escalation **tier fires** are exactly-once *in effect*: the durable `next_fire_at`
timer is claimed by N workers via `FOR UPDATE SKIP LOCKED` + a `claimed_until`
lease, and a `UNIQUE escalation_idempotency(incident_id, tier)` insert (ON CONFLICT
DO NOTHING) in the firing transaction collapses any re-fire after a crash into a
no-op. A killed worker's claim simply expires and a survivor re-claims the still-due
row — recovery needs no special on-boot path.

Notification **sends** are exactly-once *in effect* via a **two-phase SENDING
claim**: the relay atomically flips a due `outbox` row `PENDING → SENDING` (with its
own lease) and commits before calling the provider, then settles `SENDING → SENT`
after. A crash between the send and the settle leaves a recoverable `SENDING` row
that is reclaimed once its lease expires and re-sent — and the provider's
`Idempotency-Key` (Twilio) / an idempotent receiver collapses the re-send into one
delivered message. The send *invocation* is therefore at-least-once (a crash can
cause a second call), but the delivered *effect* is exactly-once. True exactly-once
*invocation* across a crash is impossible without provider cooperation (two-generals),
so this is the honest, correct guarantee.

## Reliability / chaos

Deterministic chaos tests prove both guarantees under killed workers — run them with:

```bash
make chaos          # or, on Windows: ./.venv/Scripts/python.exe -m pytest -m chaos -v
```

- **Escalation** (`cloud/tests/chaos/test_escalation_exactly_once.py`): 3 workers race
  one due incident; one is killed while holding a claim mid-transition. The survivors
  drive it to `CRITICAL_UNRESOLVED`; the test asserts **0 duplicate** (each tier event
  exactly once, `escalation_idempotency` count == fired tiers) and **0 miss**.
- **Notifier** (`cloud/tests/chaos/test_notifier_exactly_once.py`): a worker is killed
  between the provider send and the settle commit; the row is reclaimed and re-sent,
  and the test asserts exactly one delivered effect (one `messages` row) despite the
  second send invocation.

> Deferred (documented, not built in 3a): a `docker kill`-based demo against the
> compose stack and a pumba randomized-kill soak. The deterministic pytest proofs
> above are the CI-gated source of truth.

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
escalation transition) `PUBLISH` to the Redis channel `WS_REDIS_CHANNEL`
(`dashboard:incidents`) inside/after the state-change transaction. The `api` service
subscribes on startup (FastAPI lifespan) and translates each message into the WS
envelope, broadcasting to all connected sockets. Redis is **non-authoritative**: if it
is down, the API degrades to polling Postgres every `WS_FALLBACK_POLL_SECONDS` for
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
