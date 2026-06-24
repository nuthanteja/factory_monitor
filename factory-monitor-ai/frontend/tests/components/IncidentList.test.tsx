import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { IncidentList } from "../../src/components/IncidentList";
import type { Incident } from "../../src/lib/api";

const base: Incident = {
  id: "11111111-1111-4111-8111-111111111111",
  camera_id: "cam_01",
  zone_id: "zone_weld_bay",
  anomaly_type: "ppe_no_hardhat",
  rule_id: "PPE_NO_HARDHAT",
  severity: "high",
  object_class: null,
  status: "AWAITING_OPERATOR",
  current_tier: 0,
  deadline_at: null,
  tier_label: "Operator",
  created_at: "2026-06-22T10:15:03.412Z",
  snapshot_url: null,
};

describe("IncidentList", () => {
  it("renders one card per incident", () => {
    const incidents: Incident[] = [
      base,
      { ...base, id: "22222222-2222-4222-8222-222222222222", camera_id: "cam_02" },
    ];
    render(<IncidentList incidents={incidents} />);
    expect(screen.getAllByTestId("incident-card")).toHaveLength(2);
    expect(screen.getByText("cam_01")).toBeInTheDocument();
    expect(screen.getByText("cam_02")).toBeInTheDocument();
  });

  it("shows an empty-state when there are no incidents", () => {
    render(<IncidentList incidents={[]} />);
    expect(screen.queryByTestId("incident-card")).not.toBeInTheDocument();
    expect(screen.getByText(/No active incidents/i)).toBeInTheDocument();
  });
});
