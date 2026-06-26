import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDetections } from "../../src/hooks/useDetections";
import { MockWebSocket, mockWsFactory } from "../mocks/mockWebSocket";
import type { DetectionFrame } from "../../src/lib/detectionContract";

function makeFrame(seq: number): { type: string; data: DetectionFrame } {
  return {
    type: "detection.frame",
    data: {
      camera_id: "cam_01",
      ts: 1750809600.0,
      frame_w: 640,
      frame_h: 480,
      seq,
      boxes: [],
    },
  };
}

describe("useDetections", () => {
  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("connects to /ws/detections/{cameraId}", () => {
    renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory }),
    );
    const ws = MockWebSocket.last();
    expect(ws.url).toContain("/ws/detections/cam_01");
  });

  it("sends NO subscribe message on open", () => {
    renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    expect(ws.sent).toHaveLength(0);
  });

  it("starts with null and exposes frame on detection.frame message", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 1000 }),
    );
    expect(result.current).toBeNull();

    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() => ws.emit(makeFrame(1)));
    expect(result.current).not.toBeNull();
    expect(result.current?.seq).toBe(1);
  });

  it("latest-wins: second frame replaces first", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 1000 }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() => ws.emit(makeFrame(1)));
    act(() => ws.emit(makeFrame(2)));
    expect(result.current?.seq).toBe(2);
  });

  it("ignores non-detection.frame envelopes", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 1000 }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() => ws.emit({ type: "system.heartbeat", data: {} }));
    expect(result.current).toBeNull();
  });

  it("does not throw on malformed JSON", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 1000 }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    expect(() => {
      act(() => ws.onmessage?.({ data: "not-json{{{{" }));
    }).not.toThrow();
    expect(result.current).toBeNull();
  });

  it("staleness: frame becomes null after staleMs if not re-armed", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 500 }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() => ws.emit(makeFrame(1)));
    expect(result.current?.seq).toBe(1);

    act(() => vi.advanceTimersByTime(500));
    expect(result.current).toBeNull();
  });

  it("staleness: re-arm prevents clear before staleMs", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", { wsFactory: mockWsFactory, staleMs: 500 }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    act(() => ws.emit(makeFrame(1)));
    // re-arm at 400ms
    act(() => vi.advanceTimersByTime(400));
    act(() => ws.emit(makeFrame(2)));
    // original timer would fire at 500 total but was cleared
    act(() => vi.advanceTimersByTime(200)); // 600ms since start — if not cleared, would have nulled at 500
    expect(result.current?.seq).toBe(2); // still has the latest frame
    // now let the re-armed stale timer fire
    act(() => vi.advanceTimersByTime(400)); // 1000ms since re-arm
    expect(result.current).toBeNull();
  });

  it("WS close → frame becomes null + reconnects after backoff", () => {
    const { result } = renderHook(() =>
      useDetections("cam_01", {
        wsFactory: mockWsFactory,
        staleMs: 1000,
        baseBackoffMs: 100,
      }),
    );
    const ws1 = MockWebSocket.last();
    act(() => ws1.open());
    act(() => ws1.emit(makeFrame(1)));
    expect(result.current?.seq).toBe(1);

    act(() => ws1.serverClose());
    expect(result.current).toBeNull();

    act(() => vi.advanceTimersByTime(100));
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("unmount stops reconnect and clears timers", () => {
    const { unmount } = renderHook(() =>
      useDetections("cam_01", {
        wsFactory: mockWsFactory,
        staleMs: 1000,
        baseBackoffMs: 100,
      }),
    );
    const ws = MockWebSocket.last();
    act(() => ws.open());
    unmount();
    expect(ws.closed).toBe(true);
    act(() => vi.advanceTimersByTime(2000));
    expect(MockWebSocket.instances).toHaveLength(1); // no reconnect
  });
});
