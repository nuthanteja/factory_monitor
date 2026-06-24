import { render, screen } from "@testing-library/react";
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

describe("App smoke", () => {
  beforeEach(() => {
    MockWebSocket.reset();
  });

  it("renders the command center heading", () => {
    renderApp();
    expect(
      screen.getByRole("heading", { name: /command center/i }),
    ).toBeInTheDocument();
  });
});
