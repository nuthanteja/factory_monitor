"""KEDA ScaledObject + on-cluster observability manifest validation.

Docker-gated: helm template | kubeconform -strict with CRD schema-location for
keda.sh and monitoring.coreos.com resources.

Pure-text: assert ScaledObject correctness without docker.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "factory-monitor"

_DOCKER_AVAILABLE = shutil.which("docker") is not None

_CRD_SCHEMA_LOCATION = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main"
    "/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)


def _helm_template_text(extra_sets: list[str] | None = None) -> str:
    """Run helm template via docker and return the rendered YAML as a string."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{CHART_DIR}:/chart:ro",
        "alpine/helm:3.16.2",
        "template",
        "release",
        "/chart",
        "--set",
        "keda.enabled=true",
        "--set",
        "observability.podMonitor.enabled=true",
        "--set",
        "observability.prometheusRule.enabled=true",
        "--set",
        "observability.grafanaDashboards.enabled=true",
    ]
    if extra_sets:
        for s in extra_sets:
            cmd += ["--set", s]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"helm template failed (rc={result.returncode}).\nSTDERR:\n{result.stderr}"
    )
    return result.stdout


def _load_rendered_objects(text: str) -> list[dict]:
    """Parse all YAML documents from a helm template output."""
    return [doc for doc in yaml.safe_load_all(text) if doc is not None]


# ── Pure-text: ScaledObject correctness (no docker needed) ───────────────────


def test_ingest_scaledobject_has_correct_kafka_trigger() -> None:
    """Ingest ScaledObject template file must have correct kafka trigger params."""
    tpl = (CHART_DIR / "templates" / "keda" / "scaledobject-ingest.yaml").read_text(
        encoding="utf-8"
    )
    assert "consumerGroup: ingest-worker" in tpl, (
        "ingest ScaledObject must use consumerGroup: ingest-worker"
    )
    assert "topic: vision.anomalies.v1" in tpl, (
        "ingest ScaledObject must use topic: vision.anomalies.v1"
    )
    assert "maxReplicaCount: 6" in tpl, (
        "ingest ScaledObject must have maxReplicaCount: 6"
    )
    assert "type: kafka" in tpl, "ingest ScaledObject must use type: kafka trigger"
    assert "lagThreshold: \"100\"" in tpl, (
        "ingest ScaledObject must have lagThreshold: \"100\""
    )


def test_escalation_scaledobject_has_correct_prometheus_trigger() -> None:
    """Escalation ScaledObject template file must query escalation_due_rows."""
    tpl = (CHART_DIR / "templates" / "keda" / "scaledobject-escalation.yaml").read_text(
        encoding="utf-8"
    )
    assert "type: prometheus" in tpl, "escalation ScaledObject must use type: prometheus trigger"
    assert "escalation_due_rows" in tpl, (
        "escalation ScaledObject query must reference escalation_due_rows"
    )
    assert "maxReplicaCount: 4" in tpl, (
        "escalation ScaledObject must have maxReplicaCount: 4"
    )
    assert "threshold: \"50\"" in tpl, (
        "escalation ScaledObject must have threshold: \"50\""
    )


def test_scaledobject_scale_target_ref_matches_deployment_names() -> None:
    """ScaledObject scaleTargetRef names must match the worker Deployment names."""
    ingest_tpl = (CHART_DIR / "templates" / "keda" / "scaledobject-ingest.yaml").read_text(
        encoding="utf-8"
    )
    escalation_tpl = (
        CHART_DIR / "templates" / "keda" / "scaledobject-escalation.yaml"
    ).read_text(encoding="utf-8")
    ingest_deploy = (
        CHART_DIR / "templates" / "ingest-worker-deployment.yaml"
    ).read_text(encoding="utf-8")
    escalation_deploy = (
        CHART_DIR / "templates" / "escalation-worker-deployment.yaml"
    ).read_text(encoding="utf-8")

    # Deployment names are "name: ingest-worker" and "name: escalation-worker"
    assert "name: ingest-worker" in ingest_deploy, (
        "ingest-worker-deployment.yaml metadata.name must be 'ingest-worker'"
    )
    assert "name: ingest-worker" in ingest_tpl, (
        "scaledobject-ingest.yaml scaleTargetRef.name must be 'ingest-worker'"
    )
    assert "name: escalation-worker" in escalation_deploy, (
        "escalation-worker-deployment.yaml metadata.name must be 'escalation-worker'"
    )
    assert "name: escalation-worker" in escalation_tpl, (
        "scaledobject-escalation.yaml scaleTargetRef.name must be 'escalation-worker'"
    )


def test_podmonitor_relabels_job_correctly() -> None:
    """PodMonitor template must relabel job to ingest_worker/escalation_worker/notifier_worker."""
    tpl = (CHART_DIR / "templates" / "observability" / "podmonitor.yaml").read_text(
        encoding="utf-8"
    )
    assert "replacement: ingest_worker" in tpl, (
        "PodMonitor must relabel job to ingest_worker"
    )
    assert "replacement: escalation_worker" in tpl, (
        "PodMonitor must relabel job to escalation_worker"
    )
    assert "replacement: notifier_worker" in tpl, (
        "PodMonitor must relabel job to notifier_worker"
    )


def test_prometheusrule_contains_escalation_backlog_rule() -> None:
    """PrometheusRule must include the EscalationBacklog alert rule."""
    tpl = (CHART_DIR / "templates" / "observability" / "prometheusrule.yaml").read_text(
        encoding="utf-8"
    )
    assert "EscalationBacklog" in tpl, "PrometheusRule must include EscalationBacklog alert"
    assert "escalation_due_rows" in tpl, (
        "PrometheusRule EscalationBacklog must reference escalation_due_rows"
    )


def test_keda_resources_are_gated_by_keda_enabled() -> None:
    """KEDA ScaledObject templates must be gated by .Values.keda.enabled."""
    for fname in ["scaledobject-ingest.yaml", "scaledobject-escalation.yaml"]:
        tpl = (CHART_DIR / "templates" / "keda" / fname).read_text(encoding="utf-8")
        assert "keda.enabled" in tpl, f"{fname} must be gated by .Values.keda.enabled"


def test_observability_resources_are_gated() -> None:
    """Observability templates must be gated by their respective values flags."""
    podmonitor = (CHART_DIR / "templates" / "observability" / "podmonitor.yaml").read_text(
        encoding="utf-8"
    )
    assert "observability.podMonitor.enabled" in podmonitor, (
        "podmonitor.yaml must be gated by .Values.observability.podMonitor.enabled"
    )

    prom_rule = (CHART_DIR / "templates" / "observability" / "prometheusrule.yaml").read_text(
        encoding="utf-8"
    )
    assert "observability.prometheusRule.enabled" in prom_rule, (
        "prometheusrule.yaml must be gated by .Values.observability.prometheusRule.enabled"
    )

    dashboards = (CHART_DIR / "templates" / "observability" / "dashboards.yaml").read_text(
        encoding="utf-8"
    )
    assert "observability.grafanaDashboards.enabled" in dashboards, (
        "dashboards.yaml must be gated by .Values.observability.grafanaDashboards.enabled"
    )


# ── Docker-gated: helm template | kubeconform with CRD schemas ───────────────


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="docker not available on this runner")
def test_helm_template_keda_kubeconform_strict() -> None:
    """helm template (keda+obs enabled) | kubeconform -strict with CRD schema-location → rc==0."""
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
            "--set",
            "keda.enabled=true",
            "--set",
            "observability.podMonitor.enabled=true",
            "--set",
            "observability.prometheusRule.enabled=true",
            "--set",
            "observability.grafanaDashboards.enabled=true",
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
            "-schema-location",
            _CRD_SCHEMA_LOCATION,
            "-kubernetes-version",
            "1.30.0",
        ],
        stdin=helm_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

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
        f"helm template failed (rc={helm_rc}).\nSTDERR:\n{helm_stderr_text}"
    )
    assert kube_rc == 0, (
        f"kubeconform -strict failed (rc={kube_rc}).\n"
        f"STDOUT:\n{kube_out}\nSTDERR:\n{kube_err}"
    )
    assert "Errors: 0" in kube_out, (
        f"kubeconform reported non-zero errors.\nSTDOUT:\n{kube_out}"
    )
