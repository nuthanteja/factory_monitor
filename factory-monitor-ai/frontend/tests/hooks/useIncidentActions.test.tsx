import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import type { IncidentsResponse } from "../../src/lib/api";
import { INCIDENTS_QUERY_KEY } from "../../src/hooks/useIncidents";

const ackMock = vi.fn();
const resolveMock = vi.fn();
vi.mock("../../src/lib/api", () => ({
  acknowledgeIncident: (...a: unknown[]) => ackMock(...a),
  resolveIncident: (...a: unknown[]) => resolveMock(...a),
}));

import { useIncidentActions } from "../../src/hooks/useIncidentActions";

const ID = "11111111-1111-4111-8111-111111111111";
const seeded: IncidentsResponse = {
  incidents: [
    {
      id: ID,
      camera_id: "cam_03",
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

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(INCIDENTS_QUERY_KEY, seeded);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("useIncidentActions", () => {
  beforeEach(() => {
    ackMock.mockReset();
    resolveMock.mockReset();
  });

  it("acknowledge calls the API and optimistically sets status to ACK", async () => {
    ackMock.mockResolvedValue({ incident_id: ID, status: "ACK" });
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useIncidentActions(), { wrapper });

    act(() => {
      result.current.acknowledge.mutate({ id: ID });
    });

    // optimistic patch is synchronous
    const optimistic = client.getQueryData<IncidentsResponse>(INCIDENTS_QUERY_KEY);
    expect(optimistic?.incidents[0].status).toBe("ACK");

    await waitFor(() => expect(result.current.acknowledge.isSuccess).toBe(true));
    expect(ackMock).toHaveBeenCalledWith(ID);
  });

  it("rolls back the optimistic status when acknowledge fails", async () => {
    ackMock.mockRejectedValue(new Error("boom"));
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useIncidentActions(), { wrapper });

    act(() => {
      result.current.acknowledge.mutate({ id: ID });
    });
    await waitFor(() => expect(result.current.acknowledge.isError).toBe(true));

    const rolledBack = client.getQueryData<IncidentsResponse>(INCIDENTS_QUERY_KEY);
    expect(rolledBack?.incidents[0].status).toBe("AWAITING_OPERATOR");
  });

  it("resolve calls the API with the note and sets status to RESOLVED", async () => {
    resolveMock.mockResolvedValue({ incident_id: ID, status: "RESOLVED" });
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useIncidentActions(), { wrapper });

    act(() => {
      result.current.resolve.mutate({ id: ID, note: "done" });
    });
    const optimistic = client.getQueryData<IncidentsResponse>(INCIDENTS_QUERY_KEY);
    expect(optimistic?.incidents[0].status).toBe("RESOLVED");

    await waitFor(() => expect(result.current.resolve.isSuccess).toBe(true));
    expect(resolveMock).toHaveBeenCalledWith(ID, "done");
  });
});
