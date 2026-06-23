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

## Make targets

`make up` · `make down` · `make logs` · `make topics` · `make migrate` · `make test`
