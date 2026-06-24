import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useLiveIncidents } from "../../src/hooks/useLiveIncidents";
import { ServerClock } from "../../src/lib/serverClock";
import { MockWebSocket, mockWsFactory } from "../mocks/mockWebSocket";
import type { IncidentView } from "../../src/lib/wsContract";

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

const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, invalidate };
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
});
