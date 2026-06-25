"""Unit tests for OTel Collector + Tempo trace pipeline configs (pure pyyaml, no Docker)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _obs() -> Path:
    return Path(__file__).resolve().parents[3] / "observability"


def test_collector_otlp_http_4318_and_tempo_pipeline():
    cfg = yaml.safe_load((_obs() / "otel-collector" / "config.yaml").read_text())
    http = cfg["receivers"]["otlp"]["protocols"]["http"]["endpoint"]
    grpc = cfg["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"]
    assert http.endswith(":4318")  # telemetry.py posts to <endpoint>/v1/traces on :4318
    assert grpc.endswith(":4317")
    traces = cfg["service"]["pipelines"]["traces"]
    assert "batch" in traces["processors"]
    assert any("tempo" in e for e in traces["exporters"])
    assert cfg["exporters"]["otlp/tempo"]["endpoint"] == "tempo:4317"  # gRPC only


def test_tempo_local_storage_and_grpc_receiver():
    cfg = yaml.safe_load((_obs() / "tempo" / "tempo.yaml").read_text())
    assert cfg["storage"]["trace"]["backend"] == "local"
    grpc_ep = cfg["distributor"]["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"]
    assert grpc_ep.endswith(":4317")
    assert cfg["compactor"]["compaction"]["block_retention"]
