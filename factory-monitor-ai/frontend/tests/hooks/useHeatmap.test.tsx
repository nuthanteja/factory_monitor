import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useHeatmap } from "../../src/hooks/useHeatmap";
import { MockWebSocket, mockWsFactory } from "../mocks/mockWebSocket";
import type { HeatmapTick } from "../../src/lib/heatmapContract";
import type { HeatCell } from "../../src/lib/api";

function makeTick(
  camera_id: string,
  cells: { zone_id: string; count: number }[],
  ts: number = 1719050000,
): HeatmapTick {
  return { type: "heatmap.tick", data: { camera_id, cells, ts } };
}

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, client };
}

describe("useHeatmap", () => {
  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("connects and populates cells from a heatmap.tick", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );

    const ws = MockWebSocket.last();
    act(() => ws.open());
    expect(result.current.connected).toBe(true);

    act(() =>
      ws.emit(makeTick("cam_01", [{ zone_id: "zone_a", count: 3 }])),
    );
    expect(result.current.cells).toHaveLength(1);
    expect(result.current.cells[0]).toMatchObject({
      camera_id: "cam_01",
      zone_id: "zone_a",
      count: 3,
    });
  });

  it("latest-wins: a second tick for the same zone overwrites the first", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());

    act(() => ws.emit(makeTick("cam_01", [{ zone_id: "zone_a", count: 2 }])));
    act(() => ws.emit(makeTick("cam_01", [{ zone_id: "zone_a", count: 7 }])));

    const zone = result.current.cells.find(
      (c) => c.camera_id === "cam_01" && c.zone_id === "zone_a",
    );
    expect(zone?.count).toBe(7);
    // Should still be just one entry for that camera::zone key.
    const matches = result.current.cells.filter(
      (c) => c.camera_id === "cam_01" && c.zone_id === "zone_a",
    );
    expect(matches).toHaveLength(1);
  });

  it("camera-B tick leaves camera-A cells intact", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());

    act(() => ws.emit(makeTick("cam_01", [{ zone_id: "zone_a", count: 5 }])));
    act(() => ws.emit(makeTick("cam_02", [{ zone_id: "zone_b", count: 2 }])));

    const camA = result.current.cells.find((c) => c.camera_id === "cam_01");
    const camB = result.current.cells.find((c) => c.camera_id === "cam_02");
    expect(camA).toBeDefined();
    expect(camA?.count).toBe(5);
    expect(camB).toBeDefined();
    expect(camB?.count).toBe(2);
  });

  it("non-heatmap.tick messages are ignored", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());

    act(() => ws.emit({ type: "ping", data: {} }));
    act(() => ws.emit({ type: "incident.created", data: { something: true } }));

    expect(result.current.cells).toHaveLength(0);
  });

  it("malformed JSON does not throw", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());

    expect(() => {
      act(() => {
        ws.onmessage?.({ data: "{{not json}}" });
      });
    }).not.toThrow();

    expect(result.current.cells).toHaveLength(0);
  });

  it("connected becomes false and reconnect is scheduled after server close", () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory, baseBackoffMs: 100 }),
      { wrapper },
    );
    const ws1 = MockWebSocket.last();
    act(() => ws1.open());
    expect(result.current.connected).toBe(true);

    act(() => ws1.serverClose());
    expect(result.current.connected).toBe(false);

    act(() => vi.advanceTimersByTime(100));
    expect(MockWebSocket.instances).toHaveLength(2);
    const ws2 = MockWebSocket.last();
    act(() => ws2.open());
    expect(result.current.connected).toBe(true);
  });

  it("unmount tears down the socket and prevents reconnect", () => {
    const { wrapper } = makeWrapper();
    const { unmount } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory, baseBackoffMs: 100 }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    unmount();
    expect(ws.closed).toBe(true);
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("REST seed is merged in as base on first tick", () => {
    const { wrapper, client } = makeWrapper();
    // Pre-seed the REST query with data for cam_01::zone_seed.
    const seedCells: HeatCell[] = [
      { camera_id: "cam_01", zone_id: "zone_seed", count: 1, ts: 0 },
    ];
    client.setQueryData(["heatmap"], seedCells);

    const { result } = renderHook(
      () => useHeatmap({ wsFactory: mockWsFactory }),
      { wrapper },
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());

    // Before first tick: REST data is exposed.
    const beforeTick = result.current.cells.find(
      (c) => c.zone_id === "zone_seed",
    );
    expect(beforeTick?.count).toBe(1);

    // First tick for cam_02 should NOT wipe cam_01::zone_seed.
    act(() => ws.emit(makeTick("cam_02", [{ zone_id: "zone_b", count: 3 }])));

    const afterSeed = result.current.cells.find(
      (c) => c.camera_id === "cam_01" && c.zone_id === "zone_seed",
    );
    expect(afterSeed?.count).toBe(1);
    const camB = result.current.cells.find(
      (c) => c.camera_id === "cam_02" && c.zone_id === "zone_b",
    );
    expect(camB?.count).toBe(3);
  });
});
