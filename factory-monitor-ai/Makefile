COMPOSE := docker compose

.PHONY: up down logs test migrate topics fmt lint typecheck publish-fixture consume-print chaos

# Bring up all long-running services and wait for them to be healthy.
# kafka-init and migrate are one-shot services (profile "init") and must be
# run explicitly BEFORE this target so the DB and topics exist by the time
# api/ingest_worker/frontend start.
up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

# Create Kafka topics (runs the one-shot kafka-init service from profile "init").
topics:
	$(COMPOSE) --profile init up kafka-init --exit-code-from kafka-init

# Apply DB migrations (runs the one-shot migrate service from profile "init").
migrate:
	$(COMPOSE) --profile init run --rm migrate

test:
	python -m pytest

fmt:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy cloud

publish-fixture:
	python -m cloud.tools.publish_fixture

consume-print:
	python -m cloud.tools.consume_print

# Run the deterministic chaos / exactly-once reliability proofs (Docker required).
# Cross-platform: `make` wraps the same command you can run directly on Windows:
#   ./.venv/Scripts/python.exe -m pytest -m chaos -v
chaos:
	python -m pytest -m chaos -v
