import fnmatch
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _obs() -> Path:
    return Path(__file__).resolve().parents[2] / "observability"


EXPECTED = {
    "ingest_worker": "ingest_worker:9101",
    "escalation_worker": "escalation_worker:9102",
    "notifier_worker": "notifier_worker:9103",
    "edge": "edge:9108",
    "api": "api:8000",
    "kafka-exporter": "kafka-exporter:9308",
    "postgres-exporter": "postgres-exporter:9187",
    "redis-exporter": "redis-exporter:9121",
}


def test_scrape_jobs_match_real_targets():
    cfg = yaml.safe_load((_obs() / "prometheus" / "prometheus.yml").read_text())
    jobs = {j["job_name"]: j["static_configs"][0]["targets"][0] for j in cfg["scrape_configs"]}
    assert jobs == EXPECTED
    api = next(j for j in cfg["scrape_configs"] if j["job_name"] == "api")
    assert api["metrics_path"] == "/metrics"
    am_targets = cfg["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert cfg["rule_files"] and am_targets == ["alertmanager:9093"]


def test_rule_files_load_alerts_not_the_promtool_test_file() -> None:
    cfg = yaml.safe_load((_obs() / "prometheus" / "prometheus.yml").read_text())
    rules_dir = _obs() / "prometheus" / "rules"
    actual = [p.name for p in rules_dir.iterdir() if p.is_file()]
    assert "alerts_test.yml" in actual, "fixture guard: alerts_test.yml must exist or test is vacuous"
    matched: set[str] = set()
    for entry in cfg["rule_files"]:
        base = entry.rsplit("/", 1)[-1]
        matched.update(f for f in actual if fnmatch.fnmatch(f, base))
    assert "alerts.yml" in matched, "Prometheus must load the real alert rules"
    assert "alerts_test.yml" not in matched, (
        "rule_files must not load the promtool test file (Prometheus would exit 1)"
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker required")
def test_promtool_check_config():
    obs = _obs()
    # Mount the rules dir at the absolute path the config references so
    # `promtool check config` resolves and validates the rule files too — not
    # just the scrape config. (A glob that also matched the promtool test file
    # would now surface here as a load error, not silently pass.)
    rules = obs / "prometheus" / "rules"
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{obs.as_posix()}:/work",
            "-v",
            f"{rules.as_posix()}:/etc/prometheus/rules:ro",
            "prom/prometheus:v2.53.0",
            "check",
            "config",
            "/work/prometheus/prometheus.yml",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "SUCCESS" in (r.stdout + r.stderr)
