from __future__ import annotations

import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from cloud.common.logging_json import TraceJsonFormatter
from cloud.common.telemetry import reset_telemetry, setup_telemetry


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("svc", logging.INFO, __file__, 1, msg, None, None)


def test_formats_json_with_level_logger_message():
    out = TraceJsonFormatter().format(_record("hello"))
    obj = json.loads(out)
    assert obj["level"] == "INFO"
    assert obj["logger"] == "svc"
    assert obj["message"] == "hello"
    assert "timestamp" in obj


def test_includes_trace_id_under_active_span():
    reset_telemetry()
    setup_telemetry("t", exporter=InMemorySpanExporter())
    tracer = trace.get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        out = TraceJsonFormatter().format(_record("inside"))
    obj = json.loads(out)
    expected = format(span.get_span_context().trace_id, "032x")
    assert obj["trace_id"] == expected


def test_masks_phone_to_last4():
    out = TraceJsonFormatter().format(_record("sent to +15550001234 ok"))
    obj = json.loads(out)
    assert "+15550001234" not in obj["message"]
    assert "1234" in obj["message"]  # last-4 retained
