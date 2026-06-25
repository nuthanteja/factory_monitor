from __future__ import annotations

from sqlalchemy import create_engine

from cloud.common import metrics as m


def test_import_has_no_side_effects_and_metrics_registered():
    # Counters/histograms exist on the dedicated registry (sample value is 0/None until used).
    assert m.REGISTRY is not None
    # prometheus-client >=0.21 drops the _total suffix from Counter metric.name (the suffix
    # remains in sample names).  Accept both so the test is version-agnostic.
    names = {metric.name for metric in m.REGISTRY.collect()}
    assert "escalations_fired_total" in names or "escalations_fired" in names
    assert "ingest_event_to_incident_latency_seconds" in names


def test_start_metrics_server_noop_on_falsy_port():
    # Must not raise and must not bind a socket.
    m.start_metrics_server(0)
    m.start_metrics_server(None)


def test_metrics_response_content_type():
    body, content_type = m.metrics_response()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type


def test_db_gauge_collector_yields_sample_on_success():
    coll = m.DbGaugeCollector("escalation_due_rows", "help", "SELECT 7", create_engine("sqlite://"))
    families = list(coll.collect())
    assert len(families) == 1
    assert families[0].name == "escalation_due_rows"
    assert families[0].samples[0].value == 7.0


def test_db_gauge_collector_absent_on_db_error():
    class _BoomEngine:
        def connect(self):
            raise RuntimeError("db down")

    coll = m.DbGaugeCollector("outbox_pending", "help", "SELECT 1", _BoomEngine())
    assert list(coll.collect()) == []  # NO sample → series absent (never stale/zero)


def test_register_once_is_idempotent():
    coll = m.DbGaugeCollector("dup_test_gauge", "h", "SELECT 1", create_engine("sqlite://"))
    m._register_once(coll)
    m._register_once(coll)  # second call must not raise
