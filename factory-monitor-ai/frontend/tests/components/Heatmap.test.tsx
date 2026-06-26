import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { Heatmap } from "../../src/components/Heatmap";
import { seededZones, seededHeatmapResponse } from "../mocks/handlers";

// Stub global.WebSocket with a no-op so no real socket is opened.
class NoOpWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  send(..._args: unknown[]): void { /* noop */ }
  close(): void { /* noop */ }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("Heatmap", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", NoOpWebSocket);
  });

  it("renders a zone-region for every seeded zone with the correct data-count", async () => {
    render(<Heatmap />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("heatmap-wall")).toBeInTheDocument(),
    );

    const regions = screen.getAllByTestId("zone-region");
    expect(regions).toHaveLength(seededZones.length);

    // Build expected counts from the seed data.
    const countByZone = new Map<string, number>();
    for (const cam of seededHeatmapResponse.cameras) {
      for (const cell of cam.cells) {
        countByZone.set(cell.zone_id, cell.count);
      }
    }

    for (const zone of seededZones) {
      const el = regions.find(
        (r) => r.getAttribute("data-zone-id") === zone.id,
      );
      expect(el).toBeDefined();
      const expectedCount = countByZone.get(zone.id) ?? 0;
      expect(el?.getAttribute("data-count")).toBe(String(expectedCount));
    }
  });

  it("zone_entry (count=0) gets a transparent fill", async () => {
    render(<Heatmap />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("heatmap-wall")).toBeInTheDocument(),
    );

    const zeroRegion = screen
      .getAllByTestId("zone-region")
      .find((r) => r.getAttribute("data-zone-id") === "zone_entry");

    expect(zeroRegion).toBeDefined();
    expect(zeroRegion?.getAttribute("fill")).toBe("rgba(0,0,0,0)");
  });

  it("hot zones (count=5, max=5) get a red fill (hsla(0,", async () => {
    render(<Heatmap />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("heatmap-wall")).toBeInTheDocument(),
    );

    const hotRegion = screen
      .getAllByTestId("zone-region")
      .find((r) => r.getAttribute("data-zone-id") === "zone_assembly");

    expect(hotRegion).toBeDefined();
    expect(hotRegion?.getAttribute("fill")).toMatch(/^hsla\(0,/);
  });

  it("shows a loading state before zones resolve", () => {
    render(<Heatmap />, { wrapper });
    expect(screen.getByTestId("heatmap-loading")).toBeInTheDocument();
  });

  it("renders the connection pill", async () => {
    render(<Heatmap />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTestId("heatmap-wall")).toBeInTheDocument(),
    );

    expect(screen.getByTestId("heatmap-connection-pill")).toBeInTheDocument();
  });
});
