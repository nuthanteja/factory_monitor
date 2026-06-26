import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { CameraWall } from "../../src/components/CameraWall";
import { seededCameras } from "../mocks/handlers";

/** No-op WebSocket that never connects — keeps tiles hermetic in happy-dom. */
class NoOpWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = NoOpWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  close() {
    this.readyState = NoOpWebSocket.CLOSED;
  }
  send() {
    /* noop */
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("CameraWall", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", NoOpWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a VideoTile for each camera returned by /api/v1/cameras", async () => {
    render(<CameraWall />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("camera-wall")).toBeInTheDocument(),
    );

    const tiles = screen.getAllByTestId("video-tile");
    expect(tiles).toHaveLength(seededCameras.length);

    // Each camera name appears in its badge
    for (const cam of seededCameras) {
      const badges = screen.getAllByText(cam.name);
      // name-badge + fallback overlay both show the name
      expect(badges.length).toBeGreaterThanOrEqual(1);
    }
  });

  it("shows the fallback overlay (not live) because happy-dom lacks RTCPeerConnection", async () => {
    render(<CameraWall />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("camera-wall")).toBeInTheDocument(),
    );

    // Since RTCPeerConnection is undefined in happy-dom, useWhep immediately
    // transitions to "failed" — so the fallback overlay is always visible.
    const fallbacks = screen.getAllByTestId("video-fallback");
    expect(fallbacks).toHaveLength(seededCameras.length);

    const statuses = screen.getAllByTestId("whep-status");
    statuses.forEach((el) => expect(el).toHaveAttribute("data-status", "failed"));
  });

  it("renders a loading state before cameras resolve", () => {
    render(<CameraWall />, { wrapper });
    // Before the MSW handler responds, the loading state is shown.
    expect(screen.getByTestId("cameras-loading")).toBeInTheDocument();
  });
});
