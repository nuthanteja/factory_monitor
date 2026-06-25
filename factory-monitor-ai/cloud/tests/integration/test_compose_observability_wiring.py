"""Pure-YAML structural assertions for compose.observability.yaml wiring.

Checks:
- All obs-only services carry profiles: [obs].
- The five app-override services inject OTEL_EXPORTER_OTLP_ENDPOINT (bare
  base URL, no /v1/traces suffix).
- Base compose.yaml has NO OTEL_EXPORTER_OTLP_ENDPOINT on any service.
- Every config file mount in the obs stack is read-only (:ro).
- Exporter services point at the real internal addresses for kafka, postgres,
  and redis.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]  # factory-monitor-ai/

OBS_ONLY_SERVICES = {
    "otel-collector",
    "tempo",
    "prometheus",
    "alertmanager",
    "loki",
    "promtail",
    "grafana",
    "kafka-exporter",
    "postgres-exporter",
    "redis-exporter",
}

APP_OVERRIDE_SERVICES = {
    "api",
    "ingest_worker",
    "escalation_worker",
    "notifier_worker",
    "edge",
}

EXPECTED_OTLP_ENDPOINT = "http://otel-collector:4318"


def _obs() -> dict:
    return yaml.safe_load(
        (ROOT / "compose.observability.yaml").read_text(encoding="utf-8")
    )


def _base() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _service_env(svc: dict) -> dict:
    """Return environment as a plain dict regardless of list vs mapping form."""
    env = svc.get("environment") or {}
    if isinstance(env, dict):
        return env
    result: dict = {}
    for item in env:
        if "=" in item:
            k, v = item.split("=", 1)
            result[k] = v
        else:
            result[item] = None
    return result


# ---------------------------------------------------------------------------
# Obs-only services carry profiles: [obs]
# ---------------------------------------------------------------------------


def test_obs_only_services_have_obs_profile() -> None:
    services = _obs()["services"]
    for name in OBS_ONLY_SERVICES:
        assert name in services, (
            f"Expected obs service '{name}' missing from compose.observability.yaml"
        )
        profiles = services[name].get("profiles", [])
        assert "obs" in profiles, (
            f"Service '{name}' must carry profiles: [obs], got: {profiles}"
        )


# ---------------------------------------------------------------------------
# App override services must NOT have profiles (so they merge onto base)
# ---------------------------------------------------------------------------


def test_app_override_services_have_no_profiles() -> None:
    services = _obs()["services"]
    for name in APP_OVERRIDE_SERVICES:
        assert name in services, (
            f"Expected app-override service '{name}' in compose.observability.yaml"
        )
        profiles = services[name].get("profiles")
        assert not profiles, (
            f"App-override service '{name}' must NOT have profiles (merge-only), got: {profiles}"
        )


def test_app_override_services_have_no_image_or_build() -> None:
    services = _obs()["services"]
    for name in APP_OVERRIDE_SERVICES:
        svc = services[name]
        assert "image" not in svc, (
            f"App-override '{name}' must not re-declare 'image' (merge-only)"
        )
        assert "build" not in svc, (
            f"App-override '{name}' must not re-declare 'build' (merge-only)"
        )


# ---------------------------------------------------------------------------
# OTLP endpoint injected correctly — bare base URL, no /v1/traces
# ---------------------------------------------------------------------------


def test_app_overrides_set_otlp_endpoint() -> None:
    services = _obs()["services"]
    for name in APP_OVERRIDE_SERVICES:
        env = _service_env(services[name])
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env, (
            f"App-override '{name}' missing OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        val = env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        assert val == EXPECTED_OTLP_ENDPOINT, (
            f"'{name}' OTEL_EXPORTER_OTLP_ENDPOINT should be"
            f" '{EXPECTED_OTLP_ENDPOINT}', got '{val}'"
        )


def test_otlp_endpoint_has_no_v1_traces_suffix() -> None:
    services = _obs()["services"]
    for name in APP_OVERRIDE_SERVICES:
        env = _service_env(services[name])
        val = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        assert "/v1/traces" not in val, (
            f"'{name}' OTEL_EXPORTER_OTLP_ENDPOINT must be a bare base URL "
            f"(telemetry.py appends /v1/traces); got '{val}'"
        )


# ---------------------------------------------------------------------------
# Base compose.yaml has NO OTEL env on any service
# ---------------------------------------------------------------------------


def test_base_compose_has_no_otel_env() -> None:
    services = _base()["services"]
    for name, svc in services.items():
        env = _service_env(svc)
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env, (
            f"Base compose.yaml service '{name}' must NOT set OTEL_EXPORTER_OTLP_ENDPOINT; "
            "it is injected by compose.observability.yaml only"
        )


# ---------------------------------------------------------------------------
# Config file mounts are read-only
# ---------------------------------------------------------------------------


def test_config_mounts_are_readonly() -> None:
    services = _obs()["services"]
    config_paths = [
        "/etc/otelcol/config.yaml",
        "/etc/tempo/tempo.yaml",
        "/etc/prometheus/prometheus.yml",
        "/etc/prometheus/rules",
        "/etc/alertmanager/alertmanager.yml",
        "/etc/loki/loki.yaml",
        "/etc/promtail/promtail.yaml",
        "/etc/grafana/provisioning",
        "/var/lib/grafana/dashboards",
    ]
    for svc_name, svc in services.items():
        for vol in svc.get("volumes", []):
            vol_str = str(vol)
            for cfg_path in config_paths:
                if cfg_path in vol_str:
                    assert vol_str.endswith(":ro"), (
                        f"Service '{svc_name}': config mount for '{cfg_path}' "
                        f"must be read-only (:ro), got: '{vol_str}'"
                    )


# ---------------------------------------------------------------------------
# Exporter services point at real internal addresses
# ---------------------------------------------------------------------------


def test_kafka_exporter_points_at_internal_kafka() -> None:
    cmd = _obs()["services"]["kafka-exporter"].get("command", [])
    cmd_str = " ".join(str(c) for c in cmd)
    assert "kafka:9092" in cmd_str, (
        f"kafka-exporter command must reference 'kafka:9092', got: {cmd_str}"
    )


def test_postgres_exporter_dsn_uses_real_creds() -> None:
    env = _service_env(_obs()["services"]["postgres-exporter"])
    dsn = env.get("DATA_SOURCE_NAME", "")
    assert "factory:factory" in dsn, (
        f"postgres-exporter DATA_SOURCE_NAME must use factory:factory creds, got: {dsn}"
    )
    assert "postgres:5432" in dsn, (
        f"postgres-exporter DATA_SOURCE_NAME must reference postgres:5432, got: {dsn}"
    )
    assert "factory" in dsn.split("/")[-1], (
        f"postgres-exporter DATA_SOURCE_NAME must reference the 'factory' database, got: {dsn}"
    )


def test_redis_exporter_points_at_internal_redis() -> None:
    env = _service_env(_obs()["services"]["redis-exporter"])
    addr = env.get("REDIS_ADDR", "")
    assert "redis:6379" in addr, (
        f"redis-exporter REDIS_ADDR must reference 'redis:6379', got: {addr}"
    )
