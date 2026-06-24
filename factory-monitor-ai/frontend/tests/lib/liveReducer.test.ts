import { describe, it, expect } from "vitest";
import {
  applyEnvelope,
  initialLiveState,
  selectSortedIncidents,
  type LiveState,
} from "../../src/lib/liveReducer";
import type { AnyWsEnvelope, IncidentView } from "../../src/lib/wsContract";

function view(id: string, over: Partial<IncidentView> = {}): IncidentView {
  return {
    incident_id: id,
    camera_id: "cam_03",
    zone_id: "zone_weld_bay",
    rule_id: "PPE_NO_HARDHAT",
    anomaly_type: "ppe_no_hardhat",
    severity: "high",
    object_class: "person",
    status: "AWAITING_OPERATOR",
    current_tier: 0,
    deadline_at: "2026-06-22T10:20:00.000Z",
    opened_at: "2026-06-22T10:15:00.000Z",
    snapshot_url: null,
    tier_label: "Operator",
    ...over,
  };
}

const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function env<T extends AnyWsEnvelope["type"]>(
  type: T,
  seq: number,
  data: Extract<AnyWsEnvelope, { type: T }>["data"],
): AnyWsEnvelope {
  return {
    type,
    version: 1,
    seq,
    server_now: "2026-06-22T10:16:00.000Z",
    data,
  } as AnyWsEnvelope;
}

describe("applyEnvelope", () => {
  it("applies a snapshot into the incident map", () => {
    const r = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A), view(B)] }),
    );
    expect(r.applied).toBe(true);
    expect(r.gap).toBe(false);
    expect(Object.keys(r.state.incidents)).toHaveLength(2);
    expect(r.state.lastSeq).toBe(1);
  });

  it("upserts on incident.created and incident.updated", () => {
    let s: LiveState = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    s = applyEnvelope(s, env("incident.created", 2, view(B))).state;
    expect(Object.keys(s.incidents)).toHaveLength(2);
    s = applyEnvelope(
      s,
      env("incident.updated", 3, view(A, { status: "ACK" })),
    ).state;
    expect(s.incidents[A].status).toBe("ACK");
  });

  it("patches current_tier/status/deadline_at and recomputes tier_label on tier_advanced", () => {
    let s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    s = applyEnvelope(
      s,
      env("incident.tier_advanced", 2, {
        incident_id: A,
        current_tier: 1,
        status: "TIER1",
        deadline_at: "2026-06-22T10:25:00.000Z",
      }),
    ).state;
    expect(s.incidents[A].current_tier).toBe(1);
    expect(s.incidents[A].status).toBe("TIER1");
    expect(s.incidents[A].deadline_at).toBe("2026-06-22T10:25:00.000Z");
    expect(s.incidents[A].tier_label).toBe("Floor Manager");
  });

  it("marks resolved with null deadline and recomputed label", () => {
    let s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    s = applyEnvelope(
      s,
      env("incident.resolved", 2, {
        incident_id: A,
        resolved_at: "2026-06-22T10:18:00.000Z",
        resolved_by: "op-1",
      }),
    ).state;
    expect(s.incidents[A].status).toBe("RESOLVED");
    expect(s.incidents[A].deadline_at).toBeNull();
  });

  it("re-anchors deadlines on timer.snapshot", () => {
    let s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    s = applyEnvelope(
      s,
      env("timer.snapshot", 2, {
        incidents: [
          { incident_id: A, deadline_at: "2026-06-22T10:30:00.000Z", current_tier: 2 },
        ],
      }),
    ).state;
    expect(s.incidents[A].deadline_at).toBe("2026-06-22T10:30:00.000Z");
    expect(s.incidents[A].current_tier).toBe(2);
  });

  it("drops a duplicate/stale seq (idempotent re-delivery)", () => {
    const s = applyEnvelope(
      initialLiveState,
      env("snapshot", 5, { incidents: [view(A)] }),
    ).state;
    const r = applyEnvelope(s, env("incident.updated", 5, view(A, { status: "ACK" })));
    expect(r.applied).toBe(false);
    expect(r.gap).toBe(false);
    expect(r.state.incidents[A].status).toBe("AWAITING_OPERATOR");
  });

  it("flags a FORWARD seq gap but still applies and advances lastSeq", () => {
    const s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    const r = applyEnvelope(s, env("incident.created", 4, view(B))); // 2,3 missed
    expect(r.gap).toBe(true);
    expect(r.applied).toBe(true);
    expect(r.state.lastSeq).toBe(4);
    expect(r.state.incidents[B]).toBeDefined();
  });

  it("snapshot unconditionally re-anchors even when lastSeq is high (reconnect regression)", () => {
    // Session 1 reached lastSeq=50; simulate a reconnect where the server
    // resets its per-connection seq to 1.  The fresh snapshot MUST replace
    // incidents and re-anchor lastSeq — never be dropped by the stale guard.
    const staleState: LiveState = applyEnvelope(
      initialLiveState,
      env("snapshot", 50, { incidents: [view(A)] }),
    ).state;
    expect(staleState.lastSeq).toBe(50);

    const r = applyEnvelope(staleState, env("snapshot", 1, { incidents: [view(B)] }));
    expect(r.applied).toBe(true);
    expect(r.gap).toBe(false);
    expect(Object.keys(r.state.incidents)).toHaveLength(1);
    expect(r.state.incidents[B]).toBeDefined();
    expect(r.state.incidents[A]).toBeUndefined(); // old incident replaced
    expect(r.state.lastSeq).toBe(1);
  });

  it("advances on system.heartbeat without touching the map", () => {
    const s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    const r = applyEnvelope(s, env("system.heartbeat", 2, {}));
    expect(r.applied).toBe(true);
    expect(r.state.lastSeq).toBe(2);
    expect(r.state.lastServerNowIso).toBe("2026-06-22T10:16:00.000Z");
    expect(Object.keys(r.state.incidents)).toHaveLength(1);
  });

  it("is pure — does not mutate the input state", () => {
    const s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, { incidents: [view(A)] }),
    ).state;
    const incidentsBefore = s.incidents;
    const lastSeqBefore = s.lastSeq;
    applyEnvelope(s, env("incident.created", 2, view(B)));
    expect(s.incidents).toBe(incidentsBefore); // same reference — not mutated
    expect(s.lastSeq).toBe(lastSeqBefore);
  });
});

describe("selectSortedIncidents", () => {
  it("puts non-terminal before terminal, newest opened_at first", () => {
    const s = applyEnvelope(
      initialLiveState,
      env("snapshot", 1, {
        incidents: [
          view(A, { opened_at: "2026-06-22T10:00:00.000Z", status: "RESOLVED", deadline_at: null }),
          view(B, { opened_at: "2026-06-22T10:05:00.000Z", status: "AWAITING_OPERATOR" }),
        ],
      }),
    ).state;
    const ids = selectSortedIncidents(s).map((i) => i.incident_id);
    expect(ids).toEqual([B, A]); // open B first, terminal A last
  });
});
