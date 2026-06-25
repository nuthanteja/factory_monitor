// Browser tracing — fail-safe and OFF by default. Mirrors the backend's
// collector-optional philosophy: with no endpoint configured, register nothing
// (no provider, no instrumentation, no network) so the app runs standalone and
// tests never touch the network.
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  StackContextManager,
  WebTracerProvider,
} from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

let initialized = false;

/** Propagate W3C traceparent onto the app's same-origin /api calls. Exported so
 *  tests can assert the API is covered without provoking a real request. */
export const FETCH_PROPAGATION_URLS: RegExp[] = [/\/api\//];

export function setupBrowserTelemetry(): void {
  const url =
    (typeof window !== "undefined" && window.__OTEL_TRACES_URL__) ||
    import.meta.env.VITE_OTEL_TRACES_URL;
  if (!url || initialized) return;
  initialized = true;

  const provider = new WebTracerProvider({
    resource: resourceFromAttributes({
      [ATTR_SERVICE_NAME]: "factory-monitor-frontend",
    }),
    spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({ url }))],
  });
  provider.register({ contextManager: new StackContextManager() });

  registerInstrumentations({
    tracerProvider: provider,
    instrumentations: [
      new DocumentLoadInstrumentation(),
      new FetchInstrumentation({
        propagateTraceHeaderCorsUrls: FETCH_PROPAGATION_URLS,
        ignoreUrls: [/\/v1\/traces$/], // never trace the span-export POST
        clearTimingResources: true,
      }),
    ],
  });
}
