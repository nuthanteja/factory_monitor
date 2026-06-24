from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from cloud.common.telemetry import (
    extract_trace_context,
    inject_trace_headers,
    reset_telemetry,
    setup_telemetry,
)


def _exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("test-svc", exporter=exp)
    return exp


def test_span_is_exported_with_service_name():
    exp = _exporter()
    tracer = trace.get_tracer("t")
    with tracer.start_as_current_span("unit.span"):
        pass
    spans = exp.get_finished_spans()
    assert [s.name for s in spans] == ["unit.span"]
    assert spans[0].resource.attributes["service.name"] == "test-svc"


def test_inject_then_extract_round_trips_trace_id():
    _exporter()
    tracer = trace.get_tracer("t")
    with tracer.start_as_current_span("producer") as span:
        headers = inject_trace_headers()
        produced_trace_id = span.get_span_context().trace_id
    # headers is a list of (str, bytes); 'traceparent' must be present
    keys = {k for k, _ in headers}
    assert "traceparent" in keys
    ctx = extract_trace_context(headers)
    restored = trace.get_current_span(ctx).get_span_context().trace_id
    assert restored == produced_trace_id


def test_setup_without_exporter_is_inert():
    reset_telemetry()
    setup_telemetry("no-exporter")  # no endpoint, no exporter
    tracer = trace.get_tracer("t")
    # Creating spans must not raise even with no processor/exporter.
    with tracer.start_as_current_span("inert"):
        pass
