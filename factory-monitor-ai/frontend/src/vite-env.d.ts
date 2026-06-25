/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** OTLP/HTTP traces endpoint. Unset/empty ⇒ browser tracing is fully inert. */
  readonly VITE_OTEL_TRACES_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
interface Window {
  __OTEL_TRACES_URL__?: string;
}
