import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { IncidentCard } from "../../src/components/IncidentCard";
import type { Incident } from "../../src/lib/api";

const incident: Incident = {
  id: "11111111-1111-4111-8111-111111111111",
  camera_id: "cam_01",
  zone_id: "zone_weld_bay",
  anomaly_type: "ppe_no_hardhat",
  rule_id: "PPE_NO_HARDHAT",
  severity: "high",
  status: "AWAITING_OPERATOR",
  current_tier: 0,
  created_at: "2026-06-22T10:15:03.412Z",
  snapshot_url: null,
};

describe("IncidentCard", () => {
  it("renders camera, anomaly_type, severity, status, and created_at", () => {
    render(<IncidentCard incident={incident} />);
    expect(screen.getByText("cam_01")).toBeInTheDocument();
    expect(screen.getByText("zone_weld_bay")).toBeInTheDocument();
    expect(screen.getByText("ppe_no_hardhat")).toBeInTheDocument();
    expect(screen.getByText(/high/i)).toBeInTheDocument();
    expect(screen.getByText(/AWAITING_OPERATOR/)).toBeInTheDocument();
    const time = screen.getByText(
      (_content, el) =>
        el?.tagName.toLowerCase() === "time" &&
        el.getAttribute("dateTime") === "2026-06-22T10:15:03.412Z",
    );
    expect(time).toBeInTheDocument();
  });

  it("renders zone_id via incident-zone testid when zone_id is set", () => {
    render(<IncidentCard incident={incident} />);
    const zone = screen.getByTestId("incident-zone");
    expect(zone).toHaveTextContent("zone_weld_bay");
  });

  it("does not render zone when zone_id is null", () => {
    const incidentNoZone: Incident = { ...incident, zone_id: null };
    render(<IncidentCard incident={incidentNoZone} />);
    expect(screen.queryByTestId("incident-zone")).not.toBeInTheDocument();
  });

  it("exposes severity for styling via a data attribute", () => {
    render(<IncidentCard incident={incident} />);
    const card = screen.getByTestId("incident-card");
    expect(card).toHaveAttribute("data-severity", "high");
  });
});
