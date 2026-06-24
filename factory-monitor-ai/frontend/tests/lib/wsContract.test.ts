import { describe, it, expect } from "vitest";
import {
  WS_PROTOCOL_VERSION,
  isForwardGap,
  type IncidentView,
  type WsEnvelope,
  type SubscribeMessage,
} from "../../src/lib/wsContract";

describe("wsContract", () => {
  it("pins the protocol version to 1", () => {
    expect(WS_PROTOCOL_VERSION).toBe(1);
  });

  it("types a snapshot envelope carrying IncidentView[]", () => {
    const view: IncidentView = {
      incident_id: "11111111-1111-4111-8111-111111111111",
      camera_id: "cam_03",
      zone_id: "zone_weld_bay",
      rule_id: "PPE_NO_HARDHAT",
      anomaly_type: "ppe_no_hardhat",
      severity: "high",
      object_class: "person",
      status: "AWAITING_OPERATOR",
      current_tier: 0,
      deadline_at: "2026-06-22T10:20:03.412Z",
      opened_at: "2026-06-22T10:15:03.412Z",
      snapshot_url: null,
      tier_label: "Operator",
    };
    const env: WsEnvelope<"snapshot"> = {
      type: "snapshot",
      version: WS_PROTOCOL_VERSION,
      seq: 1,
      server_now: "2026-06-22T10:15:05.000Z",
      data: { incidents: [view] },
    };
    expect(env.data.incidents[0].tier_label).toBe("Operator");
    expect(env.data.incidents[0].deadline_at).toBe("2026-06-22T10:20:03.412Z");
  });

  it("types a tier_advanced envelope with the narrow payload", () => {
    const env: WsEnvelope<"incident.tier_advanced"> = {
      type: "incident.tier_advanced",
      version: 1,
      seq: 7,
      server_now: "2026-06-22T10:20:03.500Z",
      data: {
        incident_id: "11111111-1111-4111-8111-111111111111",
        current_tier: 1,
        status: "TIER1",
        deadline_at: "2026-06-22T10:25:03.412Z",
      },
    };
    expect(env.data.current_tier).toBe(1);
  });

  it("types a terminal incident with null deadline_at", () => {
    const env: WsEnvelope<"incident.resolved"> = {
      type: "incident.resolved",
      version: 1,
      seq: 9,
      server_now: "2026-06-22T10:21:00.000Z",
      data: {
        incident_id: "11111111-1111-4111-8111-111111111111",
        resolved_at: "2026-06-22T10:21:00.000Z",
        resolved_by: "operator-uuid",
      },
    };
    expect(env.data.resolved_by).toBe("operator-uuid");
  });

  it("types the subscribe client message", () => {
    const sub: SubscribeMessage = {
      action: "subscribe",
      topics: ["incidents", "timers", "system"],
      last_seq: 0,
    };
    expect(sub.action).toBe("subscribe");
  });

  describe("isForwardGap", () => {
    it("returns true for a forward gap (last=5, incoming=8)", () => {
      expect(isForwardGap(5, 8)).toBe(true);
    });

    it("returns false for next-in-sequence (last=5, incoming=6)", () => {
      expect(isForwardGap(5, 6)).toBe(false);
    });

    it("returns false for out-of-order (last=5, incoming=4)", () => {
      expect(isForwardGap(5, 4)).toBe(false);
    });

    it("returns false for duplicate (last=5, incoming=5)", () => {
      expect(isForwardGap(5, 5)).toBe(false);
    });
  });
});
