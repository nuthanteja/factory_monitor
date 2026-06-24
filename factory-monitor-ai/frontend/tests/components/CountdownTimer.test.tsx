import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { CountdownTimer } from "../../src/components/CountdownTimer";
import { formatRemaining } from "../../src/lib/formatRemaining";
import { ServerClock } from "../../src/lib/serverClock";

describe("formatRemaining", () => {
  it("formats mm:ss", () => {
    expect(formatRemaining(5000)).toBe("00:05");
    expect(formatRemaining(65000)).toBe("01:05");
    expect(formatRemaining(0)).toBe("00:00");
  });
});

describe("CountdownTimer", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function clockAt(serverIso: string, perf = 1000): ServerClock {
    const c = new ServerClock();
    c.update(serverIso, perf);
    vi.spyOn(performance, "now").mockReturnValue(perf);
    return c;
  }

  it("renders remaining time counting down toward the deadline", () => {
    // server-now anchored at T0; deadline 10s later
    const c = clockAt("2026-06-22T10:00:00.000Z", 1000);
    const deadline = "2026-06-22T10:00:10.000Z";
    render(<CountdownTimer deadlineAt={deadline} clock={c} />);
    const el = screen.getByTestId("countdown");
    expect(el).toHaveAttribute("data-state", "counting");
    expect(el).toHaveTextContent("00:10");

    // advance perf+interval by 3s -> remaining 7s
    act(() => {
      (performance.now as unknown as { mockReturnValue: (n: number) => void }).mockReturnValue(4000);
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId("countdown")).toHaveTextContent("00:07");
  });

  it("shows OVERDUE — awaiting server past the deadline for a non-terminal incident", () => {
    const c = clockAt("2026-06-22T10:00:00.000Z", 1000);
    const deadline = "2026-06-22T09:59:58.000Z"; // already 2s past
    render(<CountdownTimer deadlineAt={deadline} clock={c} terminal={false} />);
    const el = screen.getByTestId("countdown");
    expect(el).toHaveAttribute("data-state", "overdue");
    expect(el).toHaveTextContent(/OVERDUE — awaiting server/);
  });

  it("renders a static dash for a terminal incident (null deadline)", () => {
    const c = clockAt("2026-06-22T10:00:00.000Z", 1000);
    render(<CountdownTimer deadlineAt={null} clock={c} terminal />);
    const el = screen.getByTestId("countdown");
    expect(el).toHaveAttribute("data-state", "terminal");
    expect(el).toHaveTextContent("—");
  });
});
