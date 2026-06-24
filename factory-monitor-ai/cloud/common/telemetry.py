"""Shared OpenTelemetry setup — one tracer layer for every service (cloud + edge).

setup_telemetry(service_name) is idempotent and inert when no exporter/endpoint is
configured (spans are created but dropped), so production with the obs stack down —
and every existing test — keeps working untouched. The W3C TraceContext propagator
is installed globally so trace context can ride Kafka headers between services.
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()
_initialized = False


def _force_set_provider(provider: TracerProvider) -> None:
    # opentelemetry.trace.set_tracer_provider only honors the FIRST call; reset its
    # set-once guard so services (and successive test sessions) can install a provider.
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace.set_tracer_provider(provider)


def setup_telemetry(
    service_name: str,
    *,
    endpoint: str | None = None,
    exporter: SpanExporter | None = None,
) -> None:
    """Install the global TracerProvider + W3C propagator. Idempotent in production.

    exporter  → SimpleSpanProcessor (synchronous; for tests / in-memory capture).
    endpoint  → BatchSpanProcessor(OTLPSpanExporter) at <endpoint>/v1/traces (prod).
    neither   → no processor (spans created and dropped — inert, collector-optional).
    """
    global _initialized
    if _initialized and exporter is None:
        return

    set_global_textmap(_PROPAGATOR)
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
        )

    _force_set_provider(provider)
    _initialized = True


def reset_telemetry() -> None:
    """Allow the next setup_telemetry() to re-install a provider (test helper)."""
    global _initialized
    _initialized = False


def inject_trace_headers() -> list[tuple[str, bytes]]:
    """Serialize the current trace context to Kafka-style header tuples [(key, bytes)]."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return [(k, v.encode("utf-8")) for k, v in carrier.items()]


def extract_trace_context(headers: list[tuple[str, bytes]] | None) -> Context:
    """Rebuild an OTel Context from Kafka record headers (list of (key, bytes))."""
    carrier = {
        k: (v.decode("utf-8") if isinstance(v, bytes) else v) for k, v in (headers or [])
    }
    return _PROPAGATOR.extract(carrier)
