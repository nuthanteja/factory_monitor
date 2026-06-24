import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useLiveIncidents } from "../../src/hooks/useLiveIncidents";
import { ServerClock } from "../../src/lib/serverClock";
import { MockWebSocket, mockWsFactory } from "../mocks/mockWebSocket";
import type { IncidentView } from "../../src/lib/wsContract";
import type { Incident, IncidentsResponse } from "../../src/lib/api";
import { INCIDENTS_QUERY_KEY } from "../../src/hooks/useIncidents";

function view(id: string, over: Partial<IncidentView> = {}): IncidentView {
  return {
    incident_id: id,
    camera_id: "cam_03",
    zone_id: "zone_weld_bay",
    rule_id: "PPE_NO_HARDHAT",
    anomaly_type: "ppe_no_hardhat",
    severity: "high",
    object_class: "person",
    status: "AWAITING_OPERATOR",
    current_tier: 0,
    deadline_at: "2026-06-22T10:20:00.000Z",
    opened_at: "2026-06-22T10:15:00.000Z",
    snapshot_url: null,
    tier_label: "Operator",
    ...over,
  };
}

/** Build a REST Incident (matches api.Incident shape). */
function restIncident(id: string, over: Partial<Incident> = {}): Incident {
  return {
    id,
    camera_id: "cam_03",
    zone_id: "zone_weld_bay",
    rule_id: "PPE_NO_HARDHAT",
    anomaly_type: "ppe_no_hardhat",
    severity: "high",
    object_class: "person",
    status: "AWAITING_OPERATOR",
    current_tier: 0,
    deadline_at: "2026-06-22T10:20:00.000Z",
    tier_label: "Operator",
    created_at: "2026-06-22T10:15:00.000Z",
    snapshot_url: null,
    ...over,
  };
}

const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function makeWrapper(prefillIncidents?: Incident[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");

  if (prefillIncidents) {
    // Seed the REST query cache so useIncidents() returns this data immediately.
    const data: IncidentsResponse = {
      incidents: prefillIncidents,
      meta: { server_now: "2026-06-22T10:15:00.000Z" },
    };
    client.setQueryData(INCIDENTS_QUERY_KEY, data);
  }

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, invalidate, client };
}

describe("useLiveIncidents", () => {
  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("subscribes with last_seq=0 on open and applies a snapshot", async () => {
    const { wrapper } = makeWrapper();
    const clock = new ServerClock();
    const { result } = renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory, clock }),
      { wrapper },
    );

    const ws = MockWebSocket.last();
    act(() => ws.open());
    expect(JSON.parse(ws.sent[0])).toEqual({
      action: "subscribe",
      topics: ["incidents", "timers", "system"],
      last_seq: 0,
    });
    expect(result.current.connected).toBe(true);

    act(() =>
      ws.emit({
        type: "snapshot",
        version: 1,
        seq: 1,
        server_now: "2026-06-22T10:16:00.000Z",
        data: { incidents: [view(A)] },
      }),
    );
    expect(result.current.incidents).toHaveLength(1);
    expect(result.current.incidents[0].incident_id).toBe(A);
    expect(clock.samples).toBe(1);
  });

  it("invalidates the incidents query on a forward seq gap", async () => {
    const { wrapper, invalidate } = makeWrapper();
    const { result } = renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() =>
      ws.emit({
        type: "snapshot",
        version: 1,
        seq: 1,
        server_now: "2026-06-22T10:16:00.000Z",
        data: { incidents: [view(A)] },
      }),
    );
    invalidate.mockClear();
    act(() =>
      ws.emit({
        type: "incident.created",
        version: 1,
        seq: 5, // 2,3,4 missed -> gap
        server_now: "2026-06-22T10:16:01.000Z",
        data: view("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
      }),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["incidents"] });
    expect(result.current.incidents).toHaveLength(2); // still applied
  });

  it("reconnects with backoff after a server close and resubscribes", () => {
    const { wrapper } = makeWrapper();
    renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory, baseBackoffMs: 100 }),
      { wrapper },
    );
    const ws1 = MockWebSocket.last();
    act(() => ws1.open());
    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => ws1.serverClose());
    // first backoff = base * 2^0 = 100ms
    act(() => vi.advanceTimersByTime(100));
    expect(MockWebSocket.instances).toHaveLength(2);
    const ws2 = MockWebSocket.last();
    act(() => ws2.open());
    expect(JSON.parse(ws2.sent[0]).action).toBe("subscribe");
  });

  it("closes the socket and stops reconnecting on unmount", () => {
    const { wrapper } = makeWrapper();
    const { unmount } = renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory, baseBackoffMs: 100 }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    unmount();
    expect(ws.closed).toBe(true);
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(1); // no reconnect after unmount
  });

  // Fix 1: REST seed — incidents available before first WS snapshot
  it("shows REST incidents (via incidentToView) before WS snapshot; WS state takes over after", () => {
    const { wrapper } = makeWrapper([restIncident(A)]);
    const { result } = renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory }),
      { wrapper },
    );

    // Before any WS snapshot: REST data should be visible
    expect(result.current.incidents).toHaveLength(1);
    expect(result.current.incidents[0].incident_id).toBe(A);
    // incidentToView maps id -> incident_id and preserves deadline_at
    expect(result.current.incidents[0].deadline_at).toBe("2026-06-22T10:20:00.000Z");

    // Simulate WS snapshot arriving with a different set of incidents
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() =>
      ws.emit({
        type: "snapshot",
        version: 1,
        seq: 1,
        server_now: "2026-06-22T10:16:00.000Z",
        data: { incidents: [view(B)] },
      }),
    );

    // After WS snapshot: WS state is authoritative
    expect(result.current.incidents).toHaveLength(1);
    expect(result.current.incidents[0].incident_id).toBe(B);
  });

  // Fix 2: onerror triggers reconnect directly
  it("reconnects after onerror (without relying solely on onclose)", () => {
    const { wrapper } = makeWrapper();
    renderHook(
      () => useLiveIncidents({ wsFactory: mockWsFactory, baseBackoffMs: 100 }),
      { wrapper },
    );
    const ws1 = MockWebSocket.last();
    act(() => ws1.open());
    expect(MockWebSocket.instances).toHaveLength(1);

    // Trigger onerror — should schedule reconnect directly
    act(() => ws1.onerror?.());
    // Advance past the first backoff delay (100ms * 2^0 = 100ms)
    act(() => vi.advanceTimersByTime(100));
    // A second socket should be created (reconnect happened)
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  // Fix 4: backoff delay is capped at maxBackoffMs
  it("caps reconnect delay at maxBackoffMs", () => {
    const { wrapper } = makeWrapper();
    renderHook(
      () =>
        useLiveIncidents({
          wsFactory: mockWsFactory,
          baseBackoffMs: 100,
          maxBackoffMs: 400,
        }),
      { wrapper },
    );

    // Run through several close→reconnect cycles, each time advancing exactly
    // the cap (400ms) to ensure the timer always fires.
    for (let i = 0; i < 6; i++) {
      const ws = MockWebSocket.last();
      act(() => ws.open());
      act(() => ws.serverClose());
      act(() => vi.advanceTimersByTime(400));
    }

    // After 6 cycles, 7 sockets should exist (original + 6 reconnects).
    expect(MockWebSocket.instances).toHaveLength(7);

    // The 5th reconnect attempt uses delay = min(400, 100 * 2^4) = min(400, 1600) = 400ms
    // The 6th attempt uses delay = min(400, 100 * 2^5) = min(400, 3200) = 400ms
    // All succeeded within 400ms => cap is working correctly.
  });
});
