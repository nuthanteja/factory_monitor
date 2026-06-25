"""Tests for Loki + promtail observability configs (logs pillar)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _obs() -> Path:
    # cloud/tests/test_obs_logs_config.py:
    #   parents[0]=tests, [1]=cloud, [2]=factory-monitor-ai (app root)
    return Path(__file__).resolve().parents[2] / "observability"


def _loki() -> dict:
    return yaml.safe_load((_obs() / "loki" / "loki.yaml").read_text(encoding="utf-8"))


def _promtail() -> dict:
    return yaml.safe_load((_obs() / "promtail" / "promtail.yaml").read_text(encoding="utf-8"))


def _promtail_text() -> str:
    return (_obs() / "promtail" / "promtail.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Loki assertions
# ---------------------------------------------------------------------------


def test_loki_auth_disabled() -> None:
    assert _loki()["auth_enabled"] is False


def test_loki_server_port() -> None:
    assert _loki()["server"]["http_listen_port"] == 3100


def test_loki_schema_v13_tsdb() -> None:
    cfg = _loki()
    schema_cfgs = cfg["schema_config"]["configs"]
    assert schema_cfgs, "schema_config.configs must not be empty"
    entry = schema_cfgs[0]
    assert entry["store"] == "tsdb", f"expected store=tsdb, got {entry['store']}"
    assert entry["schema"] == "v13", f"expected schema=v13, got {entry['schema']}"
    assert entry["object_store"] == "filesystem"


def test_loki_retention_24h() -> None:
    cfg = _loki()
    assert cfg["limits_config"]["retention_period"] == "24h"


def test_loki_compactor_retention_enabled() -> None:
    cfg = _loki()
    assert cfg["compactor"]["retention_enabled"] is True


def test_loki_common_storage_filesystem() -> None:
    storage = _loki()["common"]["storage"]["filesystem"]
    assert "chunks_directory" in storage
    assert "rules_directory" in storage


# ---------------------------------------------------------------------------
# Promtail assertions
# ---------------------------------------------------------------------------


def test_promtail_server_port() -> None:
    assert _promtail()["server"]["http_listen_port"] == 9080


def test_promtail_loki_client() -> None:
    clients = _promtail()["clients"]
    assert any("loki" in c["url"] for c in clients), "no loki client url found"


def test_promtail_docker_sd_configs() -> None:
    scrape = _promtail()["scrape_configs"]
    assert scrape, "scrape_configs must not be empty"
    job = scrape[0]
    assert "docker_sd_configs" in job, "docker_sd_configs missing from scrape job"
    assert job["docker_sd_configs"][0]["host"] == "unix:///var/run/docker.sock"


def test_promtail_project_relabel_keep() -> None:
    scrape = _promtail()["scrape_configs"]
    relabels = scrape[0]["relabel_configs"]
    keep_rules = [r for r in relabels if r.get("action") == "keep"]
    assert keep_rules, "no keep relabel rule found"
    assert any("factory-monitor-ai" in str(r.get("regex", "")) for r in keep_rules)


def test_promtail_json_stage_extracts_trace_and_span() -> None:
    pipeline = _promtail()["scrape_configs"][0]["pipeline_stages"]
    json_stages = [s for s in pipeline if "json" in s]
    assert json_stages, "no json stage in pipeline_stages"
    exprs = json_stages[0]["json"]["expressions"]
    assert "trace_id" in exprs, "json stage must extract trace_id"
    assert "span_id" in exprs, "json stage must extract span_id"


def test_promtail_trace_id_in_labels_stage() -> None:
    pipeline = _promtail()["scrape_configs"][0]["pipeline_stages"]
    label_stages = [s for s in pipeline if "labels" in s]
    assert label_stages, "no labels stage in pipeline_stages"
    labels_map = label_stages[0]["labels"]
    assert "trace_id" in labels_map, "trace_id must appear as a Loki label"


def test_promtail_no_unconditional_labeldrop_trace_id() -> None:
    """Guard: there must be no top-level labeldrop stage that strips trace_id from all lines."""
    text = _promtail_text()
    # A blunt labeldrop at the top level of pipeline_stages would look like:
    #   - labeldrop: [trace_id]
    # We assert no such unconditional labeldrop appears (outside a match guard).
    # The simplest check: if 'labeldrop' appears in the file alongside 'trace_id',
    # it must be nested inside a 'match' block.
    if "labeldrop" not in text:
        return  # no labeldrop at all — fine
    pipeline = _promtail()["scrape_configs"][0]["pipeline_stages"]
    for stage in pipeline:
        if "labeldrop" not in stage:
            continue
        drop_list = stage.get("labeldrop", []) or []
        assert "trace_id" not in drop_list, (
            "Unconditional labeldrop of trace_id found at pipeline top level — "
            "this removes trace_id from ALL log lines and breaks trace↔logs correlation."
        )
