"""Integration test: docker compose config validation for the obs stack.

Runs `docker compose -f compose.yaml -f compose.observability.yaml
--profile obs config` and asserts:
  - return code == 0  (compose parses and merges cleanly)
  - 'otel-collector:4318' appears in the merged output (proves that the
    OTLP override env was injected onto the app services by the merge)

Skipped when Docker is not available.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # factory-monitor-ai/

_DOCKER_AVAILABLE = shutil.which("docker") is not None


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="docker not available on this runner")
def test_compose_config_merges_cleanly() -> None:
    """compose config must return 0 (valid YAML + all mounts resolve structurally)."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.observability.yaml",
            "--profile",
            "obs",
            "config",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker compose config failed (rc={result.returncode}).\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="docker not available on this runner")
def test_compose_config_otlp_endpoint_present_in_merged_output() -> None:
    """The merged config must contain 'otel-collector:4318'.

    This proves that the OTEL_EXPORTER_OTLP_ENDPOINT override was successfully
    deep-merged onto the base app service definitions.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.observability.yaml",
            "--profile",
            "obs",
            "config",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker compose config failed — cannot check merged output.\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "otel-collector:4318" in result.stdout, (
        "Merged compose config does not contain 'otel-collector:4318'.\n"
        "This means the OTEL_EXPORTER_OTLP_ENDPOINT override was NOT merged "
        "onto the app services. Check that the app override stanzas in "
        "compose.observability.yaml have no 'profiles' or 'image' keys.\n"
        f"STDOUT (first 2000 chars):\n{result.stdout[:2000]}"
    )
