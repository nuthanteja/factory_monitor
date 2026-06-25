/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://api:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: "http://api:8000",
        changeOrigin: true,
      },
      "/v1/traces": {
        target: "http://otel-collector:4318",
        changeOrigin: true,
      },
      "/ws/live": {
        target: "http://api:8000",
        changeOrigin: true,
        ws: true,
      },
      "/ws/detections": {
        target: "http://api:8000",
        changeOrigin: true,
        ws: true,
      },
      "/whep": {
        target: "http://mediamtx:8889",
        changeOrigin: true,
        rewrite: (p: string) => p.replace(/^\/whep/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "happy-dom",
    setupFiles: ["./src/setupTests.ts"],
    css: false,
  },
});
