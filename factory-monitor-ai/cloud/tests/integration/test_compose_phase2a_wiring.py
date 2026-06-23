"""Compose wiring assertions for Phase 2a services (escalation_worker + notifier_worker).

No Docker is started — these are structural YAML checks, fast and CI-friendly.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]  # factory-monitor-ai/


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _env_example() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# escalation_worker
# ---------------------------------------------------------------------------

def test_escalation_worker_service_present() -> None:
    services = _compose()["services"]
    assert "escalation_worker" in services, "escalation_worker service missing from compose.yaml"


def test_escalation_worker_build() -> None:
    svc = _compose()["services"]["escalation_worker"]
    assert svc["build"]["context"] == "."
    assert svc["build"]["dockerfile"] == "cloud/escalation_worker/Dockerfile"


def test_escalation_worker_no_ports() -> None:
    svc = _compose()["services"]["escalation_worker"]
    assert "ports" not in svc, "escalation_worker must not expose ports"


def test_escalation_worker_depends_on_postgres_and_migrate() -> None:
    dep = _compose()["services"]["escalation_worker"]["depends_on"]
    assert dep["postgres"]["condition"] == "service_healthy"
    # migrate is profile=init, so we only require postgres here (same pattern as ingest_worker).
    # The migrate step must be run manually/in CI before the worker starts.


def test_escalation_worker_env_keys() -> None:
    svc = _compose()["services"]["escalation_worker"]
    env = svc["environment"]
    env_keys = set(env.keys()) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    required = {
        "DATABASE_URL",
        "REDIS_URL",
        "NOTIFY_PROVIDER_CHAIN",
        "OPERATOR_GRACE_SECONDS",
        "ESCALATION_LEASE_SECONDS",
    }
    assert required.issubset(env_keys), f"escalation_worker missing env keys: {required - env_keys}"


def test_escalation_worker_database_url_points_to_postgres_host() -> None:
    svc = _compose()["services"]["escalation_worker"]
    env = svc["environment"]
    db_url = env["DATABASE_URL"] if isinstance(env, dict) else next(
        v for e in env for k, v in [e.split("=", 1)] if k == "DATABASE_URL"
    )
    assert "postgres:5432" in db_url, (
        f"DATABASE_URL must reference the in-network 'postgres' host, got: {db_url}"
    )


def test_escalation_worker_notify_provider_chain_default_is_console() -> None:
    svc = _compose()["services"]["escalation_worker"]
    env = svc["environment"]
    chain = env["NOTIFY_PROVIDER_CHAIN"] if isinstance(env, dict) else next(
        v for e in env for k, v in [e.split("=", 1)] if k == "NOTIFY_PROVIDER_CHAIN"
    )
    # ConsoleProvider must appear so the stack is demoable with zero external creds.
    assert "console" in chain.lower(), (
        f"NOTIFY_PROVIDER_CHAIN must include 'console' for zero-cred demo, got: {chain}"
    )


def test_escalation_worker_restart_policy() -> None:
    svc = _compose()["services"]["escalation_worker"]
    assert svc.get("restart") == "unless-stopped"


# ---------------------------------------------------------------------------
# notifier_worker
# ---------------------------------------------------------------------------

def test_notifier_worker_service_present() -> None:
    services = _compose()["services"]
    assert "notifier_worker" in services, "notifier_worker service missing from compose.yaml"


def test_notifier_worker_build() -> None:
    svc = _compose()["services"]["notifier_worker"]
    assert svc["build"]["context"] == "."
    assert svc["build"]["dockerfile"] == "cloud/notifier_worker/Dockerfile"


def test_notifier_worker_no_ports() -> None:
    svc = _compose()["services"]["notifier_worker"]
    assert "ports" not in svc, "notifier_worker must not expose ports"


def test_notifier_worker_depends_on_postgres() -> None:
    dep = _compose()["services"]["notifier_worker"]["depends_on"]
    assert dep["postgres"]["condition"] == "service_healthy"


def test_notifier_worker_env_keys() -> None:
    svc = _compose()["services"]["notifier_worker"]
    env = svc["environment"]
    env_keys = set(env.keys()) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    required = {"DATABASE_URL", "REDIS_URL", "NOTIFY_PROVIDER_CHAIN"}
    assert required.issubset(env_keys), f"notifier_worker missing env keys: {required - env_keys}"


def test_notifier_worker_database_url_points_to_postgres_host() -> None:
    svc = _compose()["services"]["notifier_worker"]
    env = svc["environment"]
    db_url = env["DATABASE_URL"] if isinstance(env, dict) else next(
        v for e in env for k, v in [e.split("=", 1)] if k == "DATABASE_URL"
    )
    assert "postgres:5432" in db_url


def test_notifier_worker_restart_policy() -> None:
    svc = _compose()["services"]["notifier_worker"]
    assert svc.get("restart") == "unless-stopped"


# ---------------------------------------------------------------------------
# .env.example declares new vars
# ---------------------------------------------------------------------------

def test_env_example_declares_phase2a_vars() -> None:
    text = _env_example()
    for key in ("NOTIFY_PROVIDER_CHAIN", "ESCALATION_LEASE_SECONDS"):
        assert key in text, f".env.example missing {key}"
