import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../src/App";

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App integration (MSW)", () => {
  it("renders seeded incidents from the mocked API", async () => {
    renderApp();
    await waitFor(() =>
      expect(screen.getAllByTestId("incident-card")).toHaveLength(2),
    );
    expect(screen.getByText("cam_01")).toBeInTheDocument();
    expect(screen.getByText("cam_02")).toBeInTheDocument();
    expect(screen.getByText("zone_intrusion")).toBeInTheDocument();
  });
});
