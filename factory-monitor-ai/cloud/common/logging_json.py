"""Structured-JSON logging that correlates with traces.

Every record becomes one JSON line carrying the active trace_id/span_id (when a span
is recording), so Grafana can jump from a span to the exact log lines. Phone numbers
are masked to last-4 (PII).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from opentelemetry import trace

_PHONE_RE = re.compile(r"\+\d{3,}(\d{4})")


def _mask_phones(text: str) -> str:
    return _PHONE_RE.sub(r"+***\1", text)


class TraceJsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object with trace correlation + PII masking."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _mask_phones(record.getMessage()),
        }
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_json_logging(level: int = logging.INFO) -> None:
    """Install a single JSON stream handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(TraceJsonFormatter())
    root.addHandler(handler)
