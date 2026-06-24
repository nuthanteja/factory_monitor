import type { Incident } from "./api";
import type { IncidentView, TierLabel } from "./wsContract";

export const WS_TOPICS = ["incidents", "timers", "system"] as const;

/**
 * Tracks an EMA-smoothed offset between the server clock (from `server_now`)
 * and the browser's monotonic `performance.now()`. The browser NEVER trusts
 * its own wall clock for deadline math — it projects the server clock forward.
 *
 *   offset = serverEpochMs - perfNowMs
 *   estimatedServerNowMs(perf) = perf + offset
 */
export class ServerClock {
  private readonly alpha: number;
  private _offsetMs = 0;
  private _samples = 0;

  constructor(alpha = 0.2) {
    this.alpha = alpha;
  }

  update(serverNowIso: string, perfNowMs: number = performance.now()): void {
    const serverEpochMs = Date.parse(serverNowIso);
    if (Number.isNaN(serverEpochMs)) {
      return;
    }
    const sample = serverEpochMs - perfNowMs;
    if (this._samples === 0) {
      this._offsetMs = sample;
    } else {
      this._offsetMs = this.alpha * sample + (1 - this.alpha) * this._offsetMs;
    }
    this._samples += 1;
  }

  estimatedServerNowMs(perfNowMs: number = performance.now()): number {
    return perfNowMs + this._offsetMs;
  }

  get offsetMs(): number {
    return this._offsetMs;
  }

  get samples(): number {
    return this._samples;
  }
}

export function tierLabelFromIncident(
  current_tier: number,
  status: string,
): TierLabel {
  if (status === "CRITICAL_UNRESOLVED") {
    return "CRITICAL";
  }
  if (current_tier >= 2) {
    return "Plant Director";
  }
  if (current_tier === 1) {
    return "Floor Manager";
  }
  return "Operator";
}

/**
 * Adapt the REST `Incident` (resync / fallback channel) into the unified
 * `IncidentView` the live store holds. Since Task 5 the REST IncidentOut
 * includes `object_class`, `deadline_at`, and `tier_label`; these are passed
 * through directly (server is authoritative). `tierLabelFromIncident` is kept
 * as a local fallback in case `tier_label` is absent on an older API version.
 */
export function incidentToView(i: Incident): IncidentView {
  return {
    incident_id: i.id,
    camera_id: i.camera_id,
    zone_id: i.zone_id,
    rule_id: i.rule_id,
    anomaly_type: i.anomaly_type,
    severity: i.severity,
    object_class: i.object_class,
    status: i.status,
    current_tier: i.current_tier,
    deadline_at: i.deadline_at ?? null,
    opened_at: i.created_at,
    snapshot_url: i.snapshot_url,
    tier_label: (i.tier_label || tierLabelFromIncident(i.current_tier, i.status)) as TierLabel,
  };
}
