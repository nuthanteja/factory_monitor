"""Validate that load/k6/slo_loadtest.js can be parsed by k6.

docker-gated: skipped when docker is unavailable (Windows dev / restricted CI).
Run:
    docker run --rm -i grafana/k6 inspect - < load/k6/slo_loadtest.js

Assertions:
- rc == 0    (script parses without error)
- options JSON contains all 3 scenario keys
- options JSON contains the http_req_duration threshold key
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
K6_SCRIPT = REPO_ROOT / "load" / "k6" / "slo_loadtest.js"

_DOCKER_AVAILABLE = shutil.which("docker") is not None


@pytest.mark.integration
@pytest.mark.skipif(
    not _DOCKER_AVAILABLE,
    reason="docker not available — k6 inspect skipped on this runner",
)
def test_k6_inspect_rc_zero() -> None:
    """k6 inspect must exit 0 (script parses cleanly)."""
    with K6_SCRIPT.open("rb") as fh:
        result = subprocess.run(
            ["docker", "run", "--rm", "-i", "grafana/k6", "inspect", "-"],
            stdin=fh,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, (
        f"k6 inspect returned rc={result.returncode}.\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not _DOCKER_AVAILABLE,
    reason="docker not available — k6 inspect skipped on this runner",
)
def test_k6_inspect_has_three_scenarios() -> None:
    """Parsed options must contain all 3 expected scenario keys."""
    with K6_SCRIPT.open("rb") as fh:
        result = subprocess.run(
            ["docker", "run", "--rm", "-i", "grafana/k6", "inspect", "-"],
            stdin=fh,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, (
        f"k6 inspect failed — cannot parse scenarios.\nSTDERR:\n{result.stderr}"
    )

    options = json.loads(result.stdout)
    scenarios = options.get("scenarios", {})

    for expected_scenario in ("ws_live", "api_read", "api_write"):
        assert expected_scenario in scenarios, (
            f"Scenario '{expected_scenario}' missing from k6 options.\n"
            f"Found: {list(scenarios.keys())}"
        )


@pytest.mark.integration
@pytest.mark.skipif(
    not _DOCKER_AVAILABLE,
    reason="docker not available — k6 inspect skipped on this runner",
)
def test_k6_inspect_has_api_p99_threshold() -> None:
    """Parsed options must contain the http_req_duration{scenario:api} p99 threshold."""
    with K6_SCRIPT.open("rb") as fh:
        result = subprocess.run(
            ["docker", "run", "--rm", "-i", "grafana/k6", "inspect", "-"],
            stdin=fh,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, (
        f"k6 inspect failed.\nSTDERR:\n{result.stderr}"
    )

    options = json.loads(result.stdout)
    thresholds = options.get("thresholds", {})

    target_key = "http_req_duration{scenario:api}"
    assert target_key in thresholds, (
        f"Threshold key '{target_key}' missing from k6 options.\n"
        f"Found threshold keys: {list(thresholds.keys())}"
    )

    threshold_rules = thresholds[target_key]
    # k6 inspect may return strings or objects; normalise to strings.
    rules_as_strings = [
        r if isinstance(r, str) else r.get("threshold", "")
        for r in threshold_rules
    ]
    assert any("p(99)<500" in rule for rule in rules_as_strings), (
        f"Expected 'p(99)<500' in threshold rules for '{target_key}'.\n"
        f"Got: {threshold_rules}"
    )
