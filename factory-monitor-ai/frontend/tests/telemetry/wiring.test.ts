import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const fe = resolve(__dirname, "../..");
const read = (p: string) => readFileSync(resolve(fe, p), "utf8");

describe("frontend telemetry wiring", () => {
  it("commits the same-origin traces endpoint in both env files", () => {
    for (const f of [".env.development", ".env.production"]) {
      expect(read(f)).toMatch(/^VITE_OTEL_TRACES_URL=\/v1\/traces\s*$/m);
    }
  });
  it("proxies /v1/traces to the collector in the vite dev server", () => {
    const cfg = read("vite.config.ts");
    expect(cfg).toContain("/v1/traces");
    expect(cfg).toMatch(/otel-collector:4318/);
  });
  it("nginx proxies /v1/traces and uses a resolver so it boots without the collector", () => {
    const conf = read("nginx.conf");
    expect(conf).toMatch(/location\s*=?\s*\/v1\/traces/);
    expect(conf).toMatch(/resolver\s+127\.0\.0\.11/); // lazy resolution → boots when collector absent
    expect(conf).toMatch(/otel-collector:4318/);
  });
});
