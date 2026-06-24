import { describe, it, expect } from "vitest";
import {
  ServerClock,
  incidentToView,
  tierLabelFromIncident,
  WS_TOPICS,
} from "../../src/lib/serverClock";
import type { Incident } from "../../src/lib/api";

const ISO = "2026-06-22T10:00:00.000Z";
const EPOCH = Date.parse(ISO); // ms

describe("ServerClock", () => {
  it("anchors offset from the first sample and projects server-now", () => {
    const c = new ServerClock();
    c.update(ISO, 1000); // perf=1000ms when server says EPOCH
    expect(c.samples).toBe(1);
    // offset = EPOCH - 1000; at perf=1500, server-now ≈ EPOCH + 500
    expect(c.estimatedServerNowMs(1500)).toBe(EPOCH + 500);
  });

  it("EMA-smooths a jittery second sample (alpha=0.2)", () => {
    const c = new ServerClock(0.2);
    c.update(ISO, 1000); // offset0 = EPOCH - 1000
    // 200ms later the server clock reads +180ms (20ms of skew/jitter):
    const iso2 = new Date(EPOCH + 180).toISOString();
    c.update(iso2, 1200); // raw sample offset = (EPOCH+180) - 1200 = EPOCH-1020
    // blended: 0.2*(EPOCH-1020) + 0.8*(EPOCH-1000) = EPOCH - 1004
    expect(c.offsetMs).toBe(EPOCH - 1004);
    expect(c.samples).toBe(2);
  });

  it("reports zero samples and a 0 offset before any update", () => {
    const c = new ServerClock();
    expect(c.samples).toBe(0);
    expect(c.offsetMs).toBe(0);
  });
});

describe("tierLabelFromIncident", () => {
  it.each([
    [0, "AWAITING_OPERATOR", "Operator"],
    [1, "TIER1", "Floor Manager"],
    [2, "TIER2", "Plant Director"],
    [3, "CRITICAL_UNRESOLVED", "CRITICAL"],
  ])("tier %i / %s -> %s", (tier, status, label) => {
    expect(tierLabelFromIncident(tier as number, status as string)).toBe(label);
  });

  it("labels terminal CRITICAL_UNRESOLVED as CRITICAL regardless of tier", () => {
    expect(tierLabelFromIncident(2, "CRITICAL_UNRESOLVED")).toBe("CRITICAL");
  });
});

describe("incidentToView", () => {
  const rest: Incident = {
    id: "11111111-1111-4111-8111-111111111111",
    camera_id: "cam_03",
    zone_id: "zone_weld_bay",
    anomaly_type: "ppe_no_hardhat",
    rule_id: "PPE_NO_HARDHAT",
    severity: "high",
    status: "TIER1",
    current_tier: 1,
    created_at: "2026-06-22T10:15:03.412Z",
    snapshot_url: null,
  };

  it("adapts REST Incident -> IncidentView (id->incident_id, derives tier_label)", () => {
    const v = incidentToView(rest);
    expect(v.incident_id).toBe(rest.id);
    expect(v.opened_at).toBe(rest.created_at);
    expect(v.tier_label).toBe("Floor Manager");
    expect(v.object_class).toBeNull();
    expect(v.deadline_at).toBeNull(); // REST list has no per-tier deadline
  });
});

describe("WS_TOPICS", () => {
  it("subscribes to incidents, timers, system", () => {
    expect(WS_TOPICS).toEqual(["incidents", "timers", "system"]);
  });
});
