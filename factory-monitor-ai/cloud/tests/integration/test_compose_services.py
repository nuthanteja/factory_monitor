from __future__ import annotations

import os

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_compose() -> dict:
    with open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_api_and_worker_services_exist():
    services = _load_compose()["services"]
    assert "api" in services
    assert "ingest_worker" in services


def test_api_service_wiring():
    svc = _load_compose()["services"]["api"]
    assert svc["build"]["dockerfile"].endswith("cloud/api/Dockerfile")
    assert any(str(p).startswith("8000:8000") or str(p) == "8000:8000" for p in svc["ports"])
    dep = svc["depends_on"]
    assert dep["postgres"]["condition"] == "service_healthy"
    assert dep["kafka"]["condition"] == "service_healthy"
    # migrate is a profile="init" one-shot service; api depends on postgres+kafka only.
    # Run `make migrate` explicitly before `make up` to apply the schema.
    assert "migrate" not in dep
    assert "healthz" in " ".join(svc["healthcheck"]["test"])
    env_keys = (
        set(svc["environment"].keys()) if isinstance(svc["environment"], dict)
        else {e.split("=", 1)[0] for e in svc["environment"]}
    )
    assert {"DATABASE_URL", "KAFKA_BOOTSTRAP_SERVERS", "REDIS_URL"}.issubset(env_keys)


def test_worker_service_wiring():
    svc = _load_compose()["services"]["ingest_worker"]
    assert svc["build"]["dockerfile"].endswith("cloud/ingest_worker/Dockerfile")
    assert "ports" not in svc
    dep = svc["depends_on"]
    assert dep["postgres"]["condition"] == "service_healthy"
    assert dep["kafka"]["condition"] == "service_healthy"
    # migrate is a profile="init" one-shot service; ingest_worker depends on postgres+kafka only.
    # Run `make migrate` explicitly before `make up` to apply the schema.
    assert "migrate" not in dep
    env_keys = (
        set(svc["environment"].keys()) if isinstance(svc["environment"], dict)
        else {e.split("=", 1)[0] for e in svc["environment"]}
    )
    assert {
        "DATABASE_URL", "KAFKA_BOOTSTRAP_SERVERS", "KAFKA_ANOMALIES_TOPIC",
        "KAFKA_DLQ_TOPIC", "OPERATOR_GRACE_SECONDS", "REDIS_URL",
    }.issubset(env_keys)
