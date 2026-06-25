from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "infra" / "kafka-init.sh"


def test_init_script_creates_both_topics() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "vision.anomalies.v1" in text
    assert "vision.anomalies.dlq" in text
    assert "vision.heatmap.v1" in text


def test_v1_topic_has_six_partitions_and_24h_retention() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--partitions 6" in text
    assert "retention.ms=86400000" in text  # 24h
    assert "cleanup.policy=delete" in text


def test_dlq_topic_has_one_partition_and_7d_retention() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--partitions 1" in text
    assert "retention.ms=604800000" in text  # 7d


def test_compose_has_kafka_init_service_depending_on_healthy_kafka() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    svc = compose["services"]["kafka-init"]
    assert svc["depends_on"]["kafka"]["condition"] == "service_healthy"
    assert svc.get("restart", "no") == "no"
