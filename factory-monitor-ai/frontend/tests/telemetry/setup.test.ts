import { afterEach, describe, expect, it, vi } from "vitest";
import { INVALID_SPAN_CONTEXT, trace } from "@opentelemetry/api";

const otlpCtor = vi.fn();
vi.mock("@opentelemetry/exporter-trace-otlp-http", () => ({
  OTLPTraceExporter: class {
    constructor(cfg: unknown) {
      otlpCtor(cfg);
    }
    export = vi.fn((_s: unknown, cb: (r: unknown) => void) => cb({ code: 0 }));
    shutdown = vi.fn(async () => {});
    forceFlush = vi.fn(async () => {});
  },
}));

afterEach(() => {
  trace.disable(); // reset the api global tracer-provider singleton
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.clearAllMocks();
});

describe("setupBrowserTelemetry", () => {
  it("is inert when VITE_OTEL_TRACES_URL is unset (no provider, no network)", async () => {
    vi.stubEnv("VITE_OTEL_TRACES_URL", "");
    const { setupBrowserTelemetry } = await import("../../src/telemetry/setup");
    setupBrowserTelemetry();
    expect(otlpCtor).not.toHaveBeenCalled();
    const span = trace.getTracer("probe").startSpan("noop");
    expect(span.isRecording()).toBe(false);
    expect(span.spanContext().traceId).toBe(INVALID_SPAN_CONTEXT.traceId);
    span.end();
  });

  it("configures the OTLP exporter url + /api propagation when enabled (no real export)", async () => {
    vi.stubEnv("VITE_OTEL_TRACES_URL", "/v1/traces");
    const mod = await import("../../src/telemetry/setup");
    mod.setupBrowserTelemetry();
    expect(otlpCtor).toHaveBeenCalledTimes(1);
    expect(otlpCtor).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/v1/traces" }),
    );
    expect(mod.FETCH_PROPAGATION_URLS).toContainEqual(/\/api\//);
    const span = trace.getTracer("probe").startSpan("real");
    expect(span.isRecording()).toBe(true);
    span.end();
  });
});
