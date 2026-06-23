import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { getIncidents, type IncidentsResponse } from "../../src/lib/api";

const sample: IncidentsResponse = {
  incidents: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      camera_id: "cam_01",
      zone_id: "zone_weld_bay",
      anomaly_type: "ppe_no_hardhat",
      rule_id: "PPE_NO_HARDHAT",
      severity: "high",
      status: "AWAITING_OPERATOR",
      current_tier: 0,
      created_at: "2026-06-22T10:15:03.412Z",
      snapshot_url: null,
    },
  ],
  meta: { server_now: "2026-06-22T10:15:05.000Z" },
};

describe("getIncidents", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs /api/v1/incidents and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(sample),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getIncidents();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/incidents",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
    expect(result.incidents).toHaveLength(1);
    expect(result.incidents[0].camera_id).toBe("cam_01");
    expect(result.meta.server_now).toBe("2026-06-22T10:15:05.000Z");
  });

  it("throws on non-OK responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.resolve({}),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getIncidents()).rejects.toThrow(/503/);
  });

  it("forwards the AbortSignal", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(sample),
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getIncidents(controller.signal);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/incidents",
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});
