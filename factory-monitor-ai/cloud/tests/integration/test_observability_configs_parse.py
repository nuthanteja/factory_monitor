"""Every observability/ config parses + the job-scope contract holds (no Docker)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def _app_root() -> Path:
    # cloud/tests/integration/test_*.py:
    #   parents[0]=integration, [1]=tests, [2]=cloud, [3]=factory-monitor-ai
    return Path(__file__).resolve().parents[3]


def test_all_yaml_json_parse():
    obs = _app_root() / "observability"
    files = list(obs.rglob("*.yml")) + list(obs.rglob("*.yaml")) + list(obs.rglob("*.json"))
    assert files, "no observability config files found"
    for f in files:
        if f.suffix == ".json":
            json.loads(f.read_text(encoding="utf-8"))
        else:
            yaml.safe_load(f.read_text(encoding="utf-8"))


def test_job_scope_contract_on_unlabeled_histograms():
    # The two UNLABELED histograms must ALWAYS be queried job-scoped (the deferred 3b.2 fix).
    # Comment lines (lines whose first non-space char is #) are skipped — documentation only.
    obs = _app_root() / "observability"
    unlabeled = ("ingest_event_to_incident_latency_seconds", "escalation_claim_latency_seconds")
    targets = list(obs.rglob("*.json")) + [obs / "prometheus" / "rules" / "alerts.yml"]
    for f in targets:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            # Skip comment lines — they're documentation, not live PromQL expressions.
            if line.lstrip().startswith("#"):
                continue
            for metric in unlabeled:
                if metric not in line:
                    continue
                assert 'job=' in line or 'job="' in line, (
                    f"{f.name}: '{metric}' used without a job-scope: {line[:120]}"
                )
