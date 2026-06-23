import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import App from "../src/App";

describe("App", () => {
  it("renders the command center heading", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: /command center/i }),
    ).toBeInTheDocument();
  });
});
