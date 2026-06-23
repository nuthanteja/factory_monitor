import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../src/App";

describe("App", () => {
  it("renders the command center heading", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    expect(
      screen.getByRole("heading", { name: /command center/i }),
    ).toBeInTheDocument();
  });
});
