# Factory Monitor AI

Edge + cloud factory-floor anomaly detection (PPE / zone intrusion) with a durable
escalation state machine. Monorepo: `edge/` (YOLOv8 + ByteTrack publisher),
`cloud/` (FastAPI API, Kafka ingest worker, Postgres source of truth), `frontend/`
(React + Vite dashboard).

## Quickstart

```bash
# 1. Python deps (Python 3.11+)
python -m pip install -e ".[dev]"

# 2. Bring up infra (postgres, kafka, redis, mediamtx)
make up

# 3. Create Kafka topics
make topics

# 4. Apply DB migrations
make migrate

# 5. Run the test suite
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

## Make targets

`make up` · `make down` · `make logs` · `make topics` · `make migrate` · `make test`
