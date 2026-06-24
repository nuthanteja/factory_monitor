import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LiveIncidentCard } from "../../src/components/LiveIncidentCard";
import { ServerClock } from "../../src/lib/serverClock";
import type { IncidentView } from "../../src/lib/wsContract";

const ID = "11111111-1111-4111-8111-111111111111";

function view(over: Partial<IncidentView> = {}): IncidentView {
  return {
    incident_id: ID,
    camera_id: "cam_03",
    zone_id: "zone_weld_bay",
    rule_id: "PPE_NO_HARDHAT",
    anomaly_type: "ppe_no_hardhat",
    severity: "high",
    object_class: "person",
    status: "TIER1",
    current_tier: 1,
    deadline_at: "2026-06-22T10:30:00.000Z",
    opened_at: "2026-06-22T10:15:00.000Z",
    snapshot_url: null,
    tier_label: "Floor Manager",
    ...over,
  };
}

function clockAt(iso: string): ServerClock {
  const c = new ServerClock();
  c.update(iso, 1000);
  vi.spyOn(performance, "now").mockReturnValue(1000);
  return c;
}

describe("LiveIncidentCard", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders the tier_label badge, camera, status, and a live countdown", () => {
    const c = clockAt("2026-06-22T10:29:50.000Z"); // 10s before deadline
    render(
      <LiveIncidentCard
        incident={view()}
        clock={c}
        onAcknowledge={() => {}}
        onResolve={() => {}}
      />,
    );
    expect(screen.getByTestId("tier-label")).toHaveTextContent("Floor Manager");
    expect(screen.getByText("cam_03")).toBeInTheDocument();
    expect(screen.getByTestId("live-incident-card")).toHaveAttribute("data-status", "TIER1");
    expect(screen.getByTestId("countdown")).toHaveTextContent("00:10");
  });

  it("fires onAcknowledge / onResolve with the incident id", () => {
    const c = clockAt("2026-06-22T10:29:50.000Z");
    const onAck = vi.fn();
    const onResolve = vi.fn();
    render(
      <LiveIncidentCard
        incident={view()}
        clock={c}
        onAcknowledge={onAck}
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByTestId("ack-button"));
    fireEvent.click(screen.getByTestId("resolve-button"));
    expect(onAck).toHaveBeenCalledWith(ID);
    expect(onResolve).toHaveBeenCalledWith(ID);
  });

  it("disables actions and shows a terminal countdown for a resolved incident", () => {
    const c = clockAt("2026-06-22T10:29:50.000Z");
    render(
      <LiveIncidentCard
        incident={view({ status: "RESOLVED", deadline_at: null, tier_label: "Operator" })}
        clock={c}
        onAcknowledge={() => {}}
        onResolve={() => {}}
      />,
    );
    expect(screen.getByTestId("ack-button")).toBeDisabled();
    expect(screen.getByTestId("resolve-button")).toBeDisabled();
    expect(screen.getByTestId("countdown")).toHaveAttribute("data-state", "terminal");
  });

  it("disables both buttons when busy (double-submit guard during mutation)", () => {
    const c = clockAt("2026-06-22T10:29:50.000Z");
    render(
      <LiveIncidentCard
        incident={view()}
        clock={c}
        onAcknowledge={() => {}}
        onResolve={() => {}}
        busy={true}
      />,
    );
    expect(screen.getByTestId("ack-button")).toBeDisabled();
    expect(screen.getByTestId("resolve-button")).toBeDisabled();
  });
});
