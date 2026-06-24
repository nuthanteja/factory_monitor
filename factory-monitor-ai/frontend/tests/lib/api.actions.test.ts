import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  acknowledgeIncident,
  resolveIncident,
} from "../../src/lib/api";

const ID = "11111111-1111-4111-8111-111111111111";

describe("acknowledgeIncident", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("POSTs acknowledge with an Idempotency-Key header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ incident_id: ID, status: "ACK" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await acknowledgeIncident(ID, "key-1");

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/incidents/${ID}/acknowledge`,
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "key-1" }),
      }),
    );
    expect(res.status).toBe("ACK");
  });

  it("throws on non-OK", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 409, json: () => Promise.resolve({}) }),
    );
    await expect(acknowledgeIncident(ID)).rejects.toThrow(/409/);
  });
});

describe("resolveIncident", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("POSTs resolve with a resolution_note body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ incident_id: ID, status: "RESOLVED" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await resolveIncident(ID, "cleared by operator", "key-2");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ resolution_note: "cleared by operator" });
    expect(init.headers["Idempotency-Key"]).toBe("key-2");
  });
});
