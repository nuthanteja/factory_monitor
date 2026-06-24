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
    // Keep server response pending so we can assert the optimistic value
    // before the mutation settles.
    let resolveAck!: (v: { incident_id: string; status: string }) => void;
    ackMock.mockReturnValue(
      new Promise((res) => {
        resolveAck = res;
      }),
    );
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useIncidentActions(), { wrapper });

    act(() => {
      result.current.acknowledge.mutate({ id: ID });
    });

    // onMutate awaits cancelQueries before patching, so the optimistic value
    // lands after the microtask queue drains — use waitFor.
    await waitFor(() =>
      expect(
        client.getQueryData<IncidentsResponse>(INCIDENTS_QUERY_KEY),
      ).toMatchObject({
        incidents: [expect.objectContaining({ id: ID, status: "ACK" })],
      }),
    );

    // Verify we haven't settled yet (still optimistic at this point).
    expect(result.current.acknowledge.isPending).toBe(true);

    // Let the server respond and confirm success.
    resolveAck({ incident_id: ID, status: "ACK" });
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
    // Keep server response pending so we can assert the optimistic value.
    let resolveResolve!: (v: { incident_id: string; status: string }) => void;
    resolveMock.mockReturnValue(
      new Promise((res) => {
        resolveResolve = res;
      }),
    );
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useIncidentActions(), { wrapper });

    act(() => {
      result.current.resolve.mutate({ id: ID, note: "done" });
    });

    // Optimistic patch lands after cancelQueries microtask — use waitFor.
    await waitFor(() =>
      expect(
        client.getQueryData<IncidentsResponse>(INCIDENTS_QUERY_KEY),
      ).toMatchObject({
        incidents: [expect.objectContaining({ id: ID, status: "RESOLVED" })],
      }),
    );

    // Still pending (optimistic, not settled).
    expect(result.current.resolve.isPending).toBe(true);

    // Let the server respond.
    resolveResolve({ incident_id: ID, status: "RESOLVED" });
    await waitFor(() => expect(result.current.resolve.isSuccess).toBe(true));
    expect(resolveMock).toHaveBeenCalledWith(ID, "done");
  });
});
