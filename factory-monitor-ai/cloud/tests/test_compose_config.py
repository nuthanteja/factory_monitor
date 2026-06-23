from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    raw = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(raw)


def test_core_services_present_with_images() -> None:
    services = _compose()["services"]
    assert services["postgres"]["image"] == "postgres:16"
    assert services["kafka"]["image"] == "apache/kafka:3.7.0"
    assert services["redis"]["image"] == "redis:7-alpine"
    assert services["mediamtx"]["image"] == "bluenviron/mediamtx:latest"


def test_each_core_service_has_healthcheck() -> None:
    services = _compose()["services"]
    for name in ("postgres", "kafka", "redis"):
        assert "healthcheck" in services[name], f"{name} missing healthcheck"
        assert "test" in services[name]["healthcheck"]


def test_kafka_kraft_single_broker_config() -> None:
    env = _compose()["services"]["kafka"]["environment"]
    assert env["KAFKA_PROCESS_ROLES"] == "broker,controller"
    assert env["KAFKA_NODE_ID"] == 1
    assert "1@kafka:9093" in env["KAFKA_CONTROLLER_QUORUM_VOTERS"]
    assert env["CLUSTER_ID"]


def test_env_example_declares_contract() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "ALEMBIC_DATABASE_URL",
        "REDIS_URL",
        "OPERATOR_GRACE_SECONDS",
    ):
        assert key in text, f".env.example missing {key}"
