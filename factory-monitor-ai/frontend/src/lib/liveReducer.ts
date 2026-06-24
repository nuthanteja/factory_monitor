import { tierLabelFromIncident } from "./serverClock";
import type { AnyWsEnvelope, IncidentView } from "./wsContract";
import { isForwardGap } from "./wsContract";

export interface LiveState {
  incidents: Record<string, IncidentView>;
  lastSeq: number;
  lastServerNowIso: string | null;
}

export const initialLiveState: LiveState = {
  incidents: {},
  lastSeq: 0,
  lastServerNowIso: null,
};

export interface ApplyResult {
  state: LiveState;
  gap: boolean;
  applied: boolean;
}

const TERMINAL = new Set(["RESOLVED", "CRITICAL_UNRESOLVED"]);

function patch(
  map: Record<string, IncidentView>,
  id: string,
  over: Partial<IncidentView>,
): Record<string, IncidentView> {
  const existing = map[id];
  if (!existing) {
    return map; // unknown row — resync will fill it
  }
  return { ...map, [id]: { ...existing, ...over } };
}

export function applyEnvelope(
  state: LiveState,
  env: AnyWsEnvelope,
): ApplyResult {
  // A snapshot is authoritative full state — it MUST re-anchor unconditionally
  // regardless of the current lastSeq (handles reconnects where the server resets
  // seq to 1 but the client still carries a stale high-water mark from before).
  if (env.type === "snapshot") {
    const incidents: Record<string, IncidentView> = {};
    for (const v of env.data.incidents) {
      incidents[v.incident_id] = v;
    }
    return {
      state: { incidents, lastSeq: env.seq, lastServerNowIso: env.server_now },
      gap: false,
      applied: true,
    };
  }

  const first = state.lastSeq === 0;

  // Idempotent re-delivery: a non-first envelope at or below lastSeq is dropped.
  if (!first && env.seq <= state.lastSeq) {
    return { state, gap: false, applied: false };
  }

  const gap = !first && isForwardGap(state.lastSeq, env.seq);

  // After the snapshot early-return above, env.type is narrowed to
  // everything except "snapshot".
  type NonSnapshotEnvelope = Exclude<AnyWsEnvelope, { type: "snapshot" }>;
  const nonSnapshotEnv = env as NonSnapshotEnvelope;
  let incidents = state.incidents;
  switch (nonSnapshotEnv.type) {
    case "incident.created":
    case "incident.updated": {
      const v = nonSnapshotEnv.data;
      incidents = { ...incidents, [v.incident_id]: v };
      break;
    }
    case "incident.tier_advanced": {
      const d = nonSnapshotEnv.data;
      incidents = patch(incidents, d.incident_id, {
        current_tier: d.current_tier,
        status: d.status,
        deadline_at: d.deadline_at,
        tier_label: tierLabelFromIncident(d.current_tier, d.status),
      });
      break;
    }
    case "incident.resolved": {
      const d = nonSnapshotEnv.data;
      incidents = patch(incidents, d.incident_id, {
        status: "RESOLVED",
        deadline_at: null,
        tier_label: tierLabelFromIncident(
          incidents[d.incident_id]?.current_tier ?? 0,
          "RESOLVED",
        ),
      });
      break;
    }
    case "timer.snapshot": {
      for (const row of nonSnapshotEnv.data.incidents) {
        incidents = patch(incidents, row.incident_id, {
          deadline_at: row.deadline_at,
          current_tier: row.current_tier,
        });
      }
      break;
    }
    case "system.heartbeat": {
      break; // clock/keepalive only
    }
  }

  return {
    state: {
      incidents,
      lastSeq: nonSnapshotEnv.seq,
      lastServerNowIso: nonSnapshotEnv.server_now,
    },
    gap,
    applied: true,
  };
}

export function selectSortedIncidents(state: LiveState): IncidentView[] {
  return Object.values(state.incidents).sort((a, b) => {
    const at = TERMINAL.has(a.status) ? 1 : 0;
    const bt = TERMINAL.has(b.status) ? 1 : 0;
    if (at !== bt) {
      return at - bt; // non-terminal (0) before terminal (1)
    }
    // newest opened_at first
    return Date.parse(b.opened_at) - Date.parse(a.opened_at);
  });
}
