"""Prometheus metrics — one shared registry for every service (cloud + edge).

Zero import side effects (no socket bind at import). start_metrics_server(port) is
inert on a falsy port so unit tests never bind a socket, mirroring the
collector-optional telemetry pattern. DB-backed backlog gauges use a scrape-time
DbGaugeCollector with its OWN dedicated sync engine (isolated from the worker's hot
asyncpg pool) and yield NO sample on a DB error — the series goes absent so the alert
fires, never masked by a stale/zero value.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import Engine, create_engine, text

logger = logging.getLogger("factory_monitor.metrics")

REGISTRY = CollectorRegistry()

# ── Ingest ───────────────────────────────────────────────────────────────────
ingest_events_consumed_total = Counter(
    "ingest_events_consumed_total",
    "Anomaly records consumed by the ingest worker, by processing outcome.",
    ["outcome"],
    registry=REGISTRY,
)
ingest_event_to_incident_latency_seconds = Histogram(
    "ingest_event_to_incident_latency_seconds",
    "Seconds from anomaly occurred_at to incident commit (SLO).",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

# ── Escalation ───────────────────────────────────────────────────────────────
escalations_fired_total = Counter(
    "escalations_fired_total",
    "Escalation tier transitions by destination tier and outcome.",
    ["tier", "result"],
    registry=REGISTRY,
)
escalation_fire_lag_seconds = Histogram(
    "escalation_fire_lag_seconds",
    "Seconds a transition fired past its next_fire_at deadline.",
    ["tier"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    registry=REGISTRY,
)
escalation_claim_latency_seconds = Histogram(
    "escalation_claim_latency_seconds",
    "Seconds for one poll_once_ids batch (claim + transition loop).",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=REGISTRY,
)

# ── Notifier ─────────────────────────────────────────────────────────────────
notifier_sends_total = Counter(
    "notifier_sends_total",
    "Outbox rows settled by the notifier, by channel and result.",
    ["channel", "result"],
    registry=REGISTRY,
)
provider_send_failures_total = Counter(
    "provider_send_failures_total",
    "Notifier send attempts that did not deliver, by provider channel.",
    ["provider"],
    registry=REGISTRY,
)
notifier_send_seconds = Histogram(
    "notifier_send_seconds",
    "Seconds for a provider send call in the notifier.",
    ["channel"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)

# ── Edge ─────────────────────────────────────────────────────────────────────
frames_in_total = Counter(
    "frames_in_total",
    "Camera frames processed by the vision engine.",
    ["camera_id"],
    registry=REGISTRY,
)
cam_last_frame_seconds = Gauge(
    "cam_last_frame_seconds",
    "Unix timestamp of the last processed frame.",
    ["camera_id"],
    registry=REGISTRY,
)
events_emitted_total = Counter(
    "events_emitted_total",
    "Confirmed anomalies emitted, by type and camera.",
    ["type", "camera_id"],
    registry=REGISTRY,
)
e2e_detect_to_publish_seconds = Histogram(
    "e2e_detect_to_publish_seconds",
    "Seconds from detect start to Kafka publish for a confirmed anomaly.",
    ["camera_id"],
    buckets=(0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=REGISTRY,
)
edge_heartbeat_total = Counter(
    "edge_heartbeat_total",
    "Dead-man's-switch liveness beat (~every 5s).",
    ["node"],
    registry=REGISTRY,
)


def start_metrics_server(port: int | None) -> None:
    """Start the prometheus_client HTTP server on `port`. No-op on a falsy port."""
    if not port:
        return
    start_http_server(port, registry=REGISTRY)


def metrics_response() -> tuple[bytes, str]:
    """(body, content_type) for a hand-rolled /metrics route (the API uses uvicorn)."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def _register_once(collector: Any) -> None:
    """Register a collector on the shared REGISTRY; ignore a duplicate (test re-import)."""
    try:
        REGISTRY.register(collector)
    except ValueError:
        logger.debug("collector already registered; skipping", exc_info=True)


class DbGaugeCollector:
    """Scrape-time gauge backed by a COUNT(*) on an injected dedicated sync engine.

    Isolated from the worker's async pool. On ANY DB error it yields NO sample (the
    series goes absent) rather than a stale or zero value.
    """

    def __init__(self, name: str, help_text: str, count_sql: str, engine: Engine) -> None:
        self._name = name
        self._help = help_text
        self._sql = count_sql
        self._engine = engine

    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            with self._engine.connect() as conn:
                count = conn.execute(text(self._sql)).scalar_one()
        except Exception:  # noqa: BLE001 — a scrape must never raise; absent-on-error
            logger.warning(
                "metrics: %s scrape query failed; emitting no sample", self._name, exc_info=True
            )
            return
        family = GaugeMetricFamily(self._name, self._help)
        family.add_metric([], float(count))
        yield family


def make_due_collector(
    name: str, help_text: str, count_sql: str, settings: Any
) -> DbGaugeCollector:
    """Build a DbGaugeCollector with a dedicated sync engine (statement timeout 2s)."""
    engine = create_engine(
        settings.alembic_database_url,
        pool_size=1,
        max_overflow=1,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=2000"},
    )
    return DbGaugeCollector(name, help_text, count_sql, engine)
