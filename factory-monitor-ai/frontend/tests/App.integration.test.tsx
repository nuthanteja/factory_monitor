import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../src/App";
import { MockWebSocket, mockWsFactory } from "./mocks/mockWebSocket";

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

describe("App integration (MSW)", () => {
  beforeEach(() => {
    MockWebSocket.reset();
  });

  it("renders REST-seeded incidents as live cards before the WS snapshot", async () => {
    renderApp();
    // useLiveIncidents seeds from REST while waiting for the first WS snapshot.
    // MSW returns 2 incidents (cam_01 / cam_02) via /api/v1/incidents.
    await waitFor(() =>
      expect(screen.getAllByTestId("live-incident-card")).toHaveLength(2),
    );
    expect(screen.getByText("cam_01")).toBeInTheDocument();
    expect(screen.getByText("cam_02")).toBeInTheDocument();
    expect(screen.getByText("zone_intrusion")).toBeInTheDocument();
  });
});
