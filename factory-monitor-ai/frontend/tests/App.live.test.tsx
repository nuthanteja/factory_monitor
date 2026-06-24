import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../src/App";
import { MockWebSocket, mockWsFactory } from "./mocks/mockWebSocket";
import type { IncidentView } from "../src/lib/wsContract";

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

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App wsFactory={mockWsFactory} />
    </QueryClientProvider>,
  );
}

describe("App live command center", () => {
  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows LIVE and renders live incident cards once the socket delivers a snapshot", async () => {
    renderApp();
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
    expect(screen.getByTestId("connection-pill")).toHaveTextContent(/LIVE/i);
    expect(screen.getByTestId("live-incident-card")).toBeInTheDocument();
    expect(screen.getByTestId("tier-label")).toHaveTextContent("Operator");
  });

  it("shows RECONNECTING before the socket opens", () => {
    renderApp();
    expect(screen.getByTestId("connection-pill")).toHaveTextContent(/RECONNECTING/i);
  });

  it("flips to RECONNECTING immediately when the socket closes", () => {
    renderApp();
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
    expect(screen.getByTestId("connection-pill")).toHaveTextContent(/LIVE/i);
    // Simulate the server closing the socket — scheduleReconnect sets connected=false
    act(() => ws.serverClose());
    expect(screen.getByTestId("connection-pill")).toHaveTextContent(
      /RECONNECTING/i,
    );
  });
});
