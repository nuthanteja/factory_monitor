"""Helm chart validation tests.

docker-gated tests: helm lint + kubeconform strict pass.
pure-text tests: infra Service names, migrate hook runs alembic.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root is three levels up from this file (cloud/tests/test_helm_chart.py)
REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "factory-monitor"
VALUES_CLOUD = CHART_DIR / "values-cloud.yaml"

_DOCKER_AVAILABLE = shutil.which("docker") is not None


# ── Docker-gated: helm lint ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="docker not available on this runner")
def test_helm_lint_clean() -> None:
    """helm lint must return rc=0 (no errors or warnings treated as errors)."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{CHART_DIR}:/chart:ro",
            "alpine/helm:3.16.2",
            "lint",
            "/chart",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm lint failed (rc={result.returncode}).\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


# ── Docker-gated: helm template | kubeconform ────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="docker not available on this runner")
def test_helm_template_kubeconform_strict() -> None:
    """helm template | kubeconform -strict must exit 0 with no errors."""
    helm_proc = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{CHART_DIR}:/chart:ro",
            "alpine/helm:3.16.2",
            "template",
            "release",
            "/chart",
            "-f",
            "/chart/values-cloud.yaml",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    kubeconform_proc = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "ghcr.io/yannh/kubeconform:latest",
            "-strict",
            "-summary",
            "-schema-location",
            "default",
            "-kubernetes-version",
            "1.30.0",
        ],
        stdin=helm_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Allow helm stdout to be read by kubeconform
    if helm_proc.stdout:
        helm_proc.stdout.close()

    kube_stdout, kube_stderr = kubeconform_proc.communicate()
    helm_proc.wait()

    helm_rc = helm_proc.returncode
    kube_rc = kubeconform_proc.returncode

    helm_stderr_text = helm_proc.stderr.read().decode() if helm_proc.stderr else ""
    kube_out = kube_stdout.decode()
    kube_err = kube_stderr.decode()

    assert helm_rc == 0, (
        f"helm template failed (rc={helm_rc}).\n"
        f"STDERR:\n{helm_stderr_text}"
    )
    assert kube_rc == 0, (
        f"kubeconform -strict failed (rc={kube_rc}).\n"
        f"STDOUT:\n{kube_out}\n"
        f"STDERR:\n{kube_err}"
    )
    # Ensure the summary line reports 0 errors (the word "Errors: 0" must appear)
    assert "Errors: 0" in kube_out, (
        f"kubeconform reported non-zero errors.\nSTDOUT:\n{kube_out}"
    )


# ── Pure-text: infra Service names ───────────────────────────────────────────


def _read_service_names() -> list[str]:
    """Parse Service 'metadata.name' values from all templates."""
    import yaml

    names: list[str] = []
    for tpl in (CHART_DIR / "templates").glob("*-service.yaml"):
        docs = list(yaml.safe_load_all(tpl.read_text(encoding="utf-8")))
        for doc in docs:
            if doc and doc.get("kind") == "Service":
                # The name may be a helm template expression; read the raw text
                pass
    # Read raw text to find names for static infra services
    for svc_file in ["postgres-service.yaml", "kafka-service.yaml", "redis-service.yaml"]:
        text = (CHART_DIR / "templates" / svc_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("name:") and "factory-monitor" not in stripped:
                names.append(stripped.split("name:", 1)[1].strip())
    return names


def test_infra_services_named_exactly() -> None:
    """postgres, kafka, redis Services must be named exactly (not release-prefixed)."""
    postgres_text = (CHART_DIR / "templates" / "postgres-service.yaml").read_text(
        encoding="utf-8"
    )
    kafka_text = (CHART_DIR / "templates" / "kafka-service.yaml").read_text(
        encoding="utf-8"
    )
    redis_text = (CHART_DIR / "templates" / "redis-service.yaml").read_text(
        encoding="utf-8"
    )

    # Each service file must declare 'name: <name>' without helm template expansion
    assert "name: postgres" in postgres_text, (
        "postgres Service is not named exactly 'postgres' — app DNS will break."
    )
    assert "name: kafka" in kafka_text, (
        "kafka Service is not named exactly 'kafka' — app DNS will break."
    )
    assert "name: redis" in redis_text, (
        "redis Service is not named exactly 'redis' — app DNS will break."
    )

    # Also confirm headless (clusterIP: None) for postgres and kafka
    assert "clusterIP: None" in postgres_text, "postgres Service must be headless."
    assert "clusterIP: None" in kafka_text, "kafka Service must be headless."


# ── Pure-text: migrate hook runs alembic ────────────────────────────────────


def test_migrate_job_runs_alembic_upgrade_head() -> None:
    """migrate Job must invoke 'alembic ... upgrade head'."""
    migrate_text = (CHART_DIR / "templates" / "migrate-job.yaml").read_text(
        encoding="utf-8"
    )
    assert "alembic" in migrate_text, "migrate Job must run alembic."
    assert "upgrade" in migrate_text, "migrate Job must run 'upgrade'."
    assert "head" in migrate_text, "migrate Job must run 'upgrade head'."
    assert "cloud/migrations/alembic.ini" in migrate_text, (
        "migrate Job must reference cloud/migrations/alembic.ini."
    )
    assert "post-install,post-upgrade" in migrate_text, (
        "migrate Job must be a post-install,post-upgrade hook."
    )


# ── Pure-text: workers use tcpSocket probes ──────────────────────────────────


def test_workers_use_tcp_socket_probes() -> None:
    """All 4 worker Deployments must use tcpSocket readiness probes, not httpGet."""
    worker_files = [
        "ingest-worker-deployment.yaml",
        "escalation-worker-deployment.yaml",
        "notifier-worker-deployment.yaml",
        "heatmap-worker-deployment.yaml",
    ]
    for fname in worker_files:
        text = (CHART_DIR / "templates" / fname).read_text(encoding="utf-8")
        assert "tcpSocket" in text, (
            f"{fname}: workers must use tcpSocket readiness probes (no HTTP health endpoint)."
        )
        assert "httpGet" not in text, (
            f"{fname}: workers must NOT use httpGet (no HTTP health endpoint)."
        )


# ── Pure-text: worker Services are headless ──────────────────────────────────


def test_worker_services_are_headless() -> None:
    """All 4 worker Services must be headless (clusterIP: None) for KEDA scraping."""
    worker_svc_files = [
        "ingest-worker-service.yaml",
        "escalation-worker-service.yaml",
        "notifier-worker-service.yaml",
        "heatmap-worker-service.yaml",
    ]
    for fname in worker_svc_files:
        text = (CHART_DIR / "templates" / fname).read_text(encoding="utf-8")
        assert "clusterIP: None" in text, (
            f"{fname}: worker Service must be headless (clusterIP: None)."
        )


# ── Pure-text: api metrics on port 8000 ──────────────────────────────────────


def test_api_metrics_annotation_on_port_8000() -> None:
    """API Deployment must scrape /metrics on port 8000, not 9104."""
    text = (CHART_DIR / "templates" / "api-deployment.yaml").read_text(encoding="utf-8")
    assert 'prometheus.io/port: "8000"' in text, (
        "API prometheus scrape port must be 8000 (not a worker metrics port)."
    )
    assert 'prometheus.io/path: "/metrics"' in text, (
        "API must have prometheus.io/path annotation for /metrics."
    )
