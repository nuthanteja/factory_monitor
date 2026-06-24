import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import type { IncidentsResponse } from "../../src/lib/api";

const getIncidentsMock = vi.fn();
vi.mock("../../src/lib/api", () => ({
  getIncidents: (...args: unknown[]) => getIncidentsMock(...args),
}));

import {
  useIncidents,
  INCIDENTS_POLL_MS,
  INCIDENTS_QUERY_KEY,
} from "../../src/hooks/useIncidents";

const sample: IncidentsResponse = {
  incidents: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      camera_id: "cam_01",
      zone_id: "zone_weld_bay",
      anomaly_type: "ppe_no_hardhat",
      rule_id: "PPE_NO_HARDHAT",
      severity: "high",
      object_class: null,
      status: "AWAITING_OPERATOR",
      current_tier: 0,
      deadline_at: null,
      tier_label: "Operator",
      created_at: "2026-06-22T10:15:03.412Z",
      snapshot_url: null,
    },
  ],
  meta: { server_now: "2026-06-22T10:15:05.000Z" },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("useIncidents", () => {
  beforeEach(() => {
    getIncidentsMock.mockReset();
  });

  it("exposes the canonical poll interval and query key", () => {
    expect(INCIDENTS_POLL_MS).toBe(2000);
    expect(INCIDENTS_QUERY_KEY).toEqual(["incidents"]);
  });

  it("loads incidents via getIncidents", async () => {
    getIncidentsMock.mockResolvedValue(sample);

    const { result } = renderHook(() => useIncidents(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getIncidentsMock).toHaveBeenCalledTimes(1);
    expect(result.current.data?.incidents[0].camera_id).toBe("cam_01");
  });

  it("surfaces errors from the client", async () => {
    getIncidentsMock.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useIncidents(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("boom");
  });
});
