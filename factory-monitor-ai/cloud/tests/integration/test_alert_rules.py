"""Docker-gated tests: promtool check/test rules + amtool check-config.

These tests require Docker on the PATH and pull prom/prometheus:v2.53.0 and
prom/alertmanager:v0.27.0 (network access on first run).  They are guarded by
the `integration` mark AND a `skipif` so they are skipped silently when Docker
is absent (e.g. unit-only CI).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _obs() -> Path:
    # cloud/tests/integration/test_*.py:
    #   parents[0]=integration, [1]=tests, [2]=cloud, [3]=factory-monitor-ai
    return Path(__file__).resolve().parents[3] / "observability"


requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker required",
)


def _promtool(*args: str) -> subprocess.CompletedProcess[str]:
    obs = _obs()
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{obs.as_posix()}:/work",
            "prom/prometheus:v2.53.0",
            *args,
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.integration
@requires_docker
def test_promtool_check_rules() -> None:
    r = _promtool("check", "rules", "/work/prometheus/rules/alerts.yml")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "SUCCESS" in (r.stdout + r.stderr)


@pytest.mark.integration
@requires_docker
def test_promtool_test_rules_fire_and_silence() -> None:
    r = _promtool("test", "rules", "/work/prometheus/rules/alerts_test.yml")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "SUCCESS" in (r.stdout + r.stderr)


@pytest.mark.integration
@requires_docker
def test_amtool_check_config() -> None:
    obs = _obs()
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "amtool",
            "-v",
            f"{obs.as_posix()}:/work",
            "prom/alertmanager:v0.27.0",
            "check-config",
            "/work/alertmanager/alertmanager.yml",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
