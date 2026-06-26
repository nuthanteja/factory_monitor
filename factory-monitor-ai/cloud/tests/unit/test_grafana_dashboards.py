"""Grafana provisioning + 6 dashboards: parse, uid-uniqueness, metric validity, job-scope."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from cloud.common.metrics import REGISTRY


def _app_root() -> Path:
    # cloud/tests/unit/test_grafana_dashboards.py:
    #   parents[0]=unit, [1]=tests, [2]=cloud, [3]=factory-monitor-ai
    return Path(__file__).resolve().parents[3]


def _grafana_root() -> Path:
    return _app_root() / "observability" / "grafana"


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_dashboards() -> list[dict]:
    db_dir = _grafana_root() / "dashboards"
    files = sorted(db_dir.glob("*.json"))
    assert len(files) == 6, f"expected 6 dashboard JSON files, got {len(files)}: {files}"
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def _build_allowed_metrics() -> set[str]:
    """Build the set of allowed metric name tokens from REGISTRY + exporters + DB-backed gauges."""
    allowed: set[str] = set()
    for metric in REGISTRY.collect():
        base = metric.name
        allowed.add(base)
        # prometheus_client may strip _total from Counter.name; add both
        if not base.endswith("_total"):
            allowed.add(base + "_total")
        # histogram sub-series
        for suffix in ("_bucket", "_count", "_sum", "_total"):
            if not base.endswith(suffix):
                allowed.add(base + suffix)
    # DB-backed gauges (absent-on-error collectors registered at runtime — not in REGISTRY here)
    allowed.update({"escalation_due_rows", "outbox_pending"})
    # Exporter / meta metrics
    allowed.update({
        "up",
        "kafka_consumergroup_lag",
        "pg_stat_database_numbackends",
        "redis_memory_used_bytes",
        "redis_connected_clients",
        "kube_horizontalpodautoscaler_status_current_replicas",
    })
    return allowed


# PromQL functions, aggregation operators, and label matchers that look like identifiers
# but are NOT metric names.
_PROMQL_KEYWORDS = frozenset({
    "sum", "rate", "max", "min", "avg", "count", "increase", "histogram_quantile",
    "clamp_min", "clamp_max", "by", "le", "on", "without", "ignoring", "group_left",
    "group_right", "offset", "bool", "and", "or", "unless", "time", "vector",
    "absent", "absent_over_time", "delta", "idelta", "irate", "label_replace",
    "label_join", "predict_linear", "resets", "changes", "deriv", "exp", "ln",
    "log2", "log10", "ceil", "floor", "round", "sqrt", "sort", "sort_desc",
    "topk", "bottomk", "quantile", "stddev", "stdvar",
    # result / refId labels
    "consumergroup", "topic", "instance", "job", "datname", "provider",
    "tier", "result", "outcome", "camera_id", "node", "channel", "type",
    "horizontalpodautoscaler",
})

_TOKEN_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\b(?!\s*\()")


def _extract_metric_tokens(expr: str) -> list[str]:
    """Return identifier tokens from a PromQL expr that could be metric names."""
    # Strip label matchers to avoid false-positives on label values
    # e.g. {job="ingest_worker"} — remove string literals
    expr_no_strings = re.sub(r'"[^"]*"', '""', expr)
    tokens = _TOKEN_RE.findall(expr_no_strings)
    return [t for t in tokens if t not in _PROMQL_KEYWORDS and not t.startswith("__")]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_datasources_yaml_parses():
    path = _grafana_root() / "provisioning" / "datasources" / "datasources.yaml"
    assert path.exists(), f"missing: {path}"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["apiVersion"] == 1
    uids = [ds["uid"] for ds in cfg["datasources"]]
    assert "prometheus" in uids
    assert "tempo" in uids
    assert "loki" in uids


def test_dashboards_yaml_parses():
    path = _grafana_root() / "provisioning" / "dashboards" / "dashboards.yaml"
    assert path.exists(), f"missing: {path}"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["apiVersion"] == 1
    names = [p["name"] for p in cfg["providers"]]
    assert "factory-monitor" in names


def test_all_dashboards_parse_and_have_unique_uids():
    dashboards = _load_dashboards()
    uids = [d["uid"] for d in dashboards]
    assert len(uids) == len(set(uids)), f"duplicate uids: {uids}"
    for d in dashboards:
        assert d.get("schemaVersion") == 39, f"{d['uid']}: wrong schemaVersion"
        assert "factory-monitor" in d.get("tags", []), f"{d['uid']}: missing factory-monitor tag"
        assert d.get("timezone") == "browser", f"{d['uid']}: timezone != browser"


def test_all_panel_targets_reference_valid_metrics():
    allowed = _build_allowed_metrics()
    dashboards = _load_dashboards()
    violations: list[str] = []
    for d in dashboards:
        for panel in d.get("panels", []):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for token in _extract_metric_tokens(expr):
                    if token not in allowed:
                        violations.append(
                            f"{d['uid']} / panel '{panel['title']}' / refId {target['refId']}: "
                            f"unknown metric token '{token}' in expr: {expr}"
                        )
    assert not violations, "Panel targets reference unknown metrics:\n" + "\n".join(violations)


def test_unlabeled_histograms_are_job_scoped():
    """ingest_event_to_incident_latency_seconds and escalation_claim_latency_seconds
    must never appear in a PromQL expr without a job= label selector."""
    unlabeled = (
        "ingest_event_to_incident_latency_seconds",
        "escalation_claim_latency_seconds",
    )
    dashboards = _load_dashboards()
    violations: list[str] = []
    for d in dashboards:
        for panel in d.get("panels", []):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for metric in unlabeled:
                    if metric not in expr:
                        continue
                    if 'job=' not in expr and 'job="' not in expr:
                        violations.append(
                            f"{d['uid']} / panel '{panel['title']}': "
                            f"'{metric}' used without job-scope in: {expr}"
                        )
    assert not violations, "Unlabeled histograms missing job-scope:\n" + "\n".join(violations)


def test_dashboard_05_errors_use_result_not_sent_label():
    """Dashboard 05 error rate must filter result!="sent", never result!="ok"."""
    dashboards = _load_dashboards()
    d05 = next(d for d in dashboards if d["uid"] == "fm-slo-golden-signals")
    all_exprs = [
        t["expr"]
        for panel in d05["panels"]
        for t in panel.get("targets", [])
    ]
    combined = "\n".join(all_exprs)
    assert 'result!="ok"' not in combined, (
        "Dashboard 05 must use result!=\"sent\" (not result!=\"ok\") for notifier error rate"
    )
    assert 'result!="sent"' in combined, (
        "Dashboard 05 must include result!=\"sent\" in the notifier error rate expr"
    )
