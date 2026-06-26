COMPOSE := docker compose

.PHONY: up down logs test migrate topics fmt lint typecheck publish-fixture consume-print chaos k8s-validate

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

# Validate k8s / Helm / KEDA / k6 artifacts without a live cluster.
#
# Requires on PATH: helm (3.14+), kubeconform (0.6+), k6 (0.51+), python3.
# Primarily for Linux/CI; on Windows use WSL2 or run commands individually.
# On first run kubeconform fetches the KEDA CRD schemas from
#   https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/…
# so a one-time internet connection is needed.
k8s-validate:
	@echo "=== helm lint ==="
	helm lint deploy/helm/factory-monitor
	@echo "=== kubeconform (k8s 1.30 + KEDA CRDs) ==="
	helm template release deploy/helm/factory-monitor \
		-f deploy/helm/factory-monitor/values-cloud.yaml \
		--set keda.enabled=true \
		--set observability.podMonitor.enabled=true \
		--set observability.prometheusRule.enabled=true \
		--set observability.grafanaDashboards.enabled=true \
	| kubeconform \
		-strict \
		-summary \
		-kubernetes-version 1.30.0 \
		-schema-location default \
		-schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
	@echo "=== k6 inspect ==="
	k6 inspect load/k6/slo_loadtest.js
	@echo "=== producer schema tests ==="
	python -m pytest cloud/tests/test_load_producer.py -v
	@echo "=== k8s-validate: all checks passed ==="
