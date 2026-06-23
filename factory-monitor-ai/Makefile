COMPOSE := docker compose

.PHONY: up down logs test migrate topics fmt lint typecheck

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

topics:
	$(COMPOSE) up kafka-init --exit-code-from kafka-init

migrate:
	$(COMPOSE) run --rm migrate

test:
	python -m pytest

fmt:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy cloud
