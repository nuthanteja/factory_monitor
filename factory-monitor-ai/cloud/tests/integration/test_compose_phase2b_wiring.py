"""Compose + config wiring assertions for Phase 2b (the /ws/live WS layer).

Pure structural YAML/text checks — no Docker started.  /ws/live is served by
the EXISTING api service, so we assert no new service appears and the api
service carries REDIS_URL + the WS env vars.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]  # factory-monitor-ai/


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _env_example() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def _api_env() -> dict:
    env = _compose()["services"]["api"]["environment"]
    if isinstance(env, dict):
        return env
    return {k: v for e in env for k, v in [e.split("=", 1)]}


def test_no_new_ws_service_added() -> None:
    # /ws/live is served by the api service; Phase 2b must NOT add a ws service.
    services = set(_compose()["services"].keys())
    assert "ws" not in services and "websocket" not in services, (
        "/ws/live is served in-process by the api service; no separate ws service"
    )


def test_api_service_has_redis_url() -> None:
    env = _api_env()
    assert "REDIS_URL" in env, "api must have REDIS_URL for the WS Redis fan-out"
    assert "redis:6379" in env["REDIS_URL"], (
        f"REDIS_URL must reference the in-network 'redis' host, got {env['REDIS_URL']}"
    )


def test_api_service_has_ws_env_keys() -> None:
    env = _api_env()
    required = {"WS_CHANNEL", "WS_POLL_INTERVAL_SECONDS"}
    missing = required - set(env.keys())
    assert not missing, f"api missing WS env keys: {missing}"


def test_api_ws_channel_matches_dashboard_incidents() -> None:
    env = _api_env()
    assert env["WS_CHANNEL"] == "dashboard:incidents", (
        "WS_CHANNEL must match the producer PUBLISH channel name (dashboard:incidents)"
    )


def test_env_example_declares_phase2b_vars() -> None:
    text = _env_example()
    for key in ("WS_CHANNEL", "WS_POLL_INTERVAL_SECONDS"):
        assert key in text, f".env.example missing {key}"


def test_pyproject_declares_redis_runtime_dep() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "redis>=5" in pyproject, "redis>=5 must be a runtime dependency for the WS fan-out"
