// TypeScript mirror of the LOCKED WebSocket contract (design §5.5).
// This module is the frontend source of truth; the server (slice-1) must
// emit envelopes that match these shapes exactly.

import type { Severity } from "./api";

export const WS_PROTOCOL_VERSION = 1;

export type TierLabel =
  | "Operator"
  | "Floor Manager"
  | "Plant Director"
  | "CRITICAL";

export type WsType =
  | "snapshot"
  | "incident.created"
  | "incident.updated"
  | "incident.tier_advanced"
  | "incident.resolved"
  | "timer.snapshot"
  | "system.heartbeat";

export interface IncidentView {
  incident_id: string;
  camera_id: string;
  zone_id: string | null;
  rule_id: string;
  anomaly_type: string;
  severity: Severity;
  object_class: string | null;
  status: string;
  current_tier: number;
  /** ABSOLUTE server deadline for the current tier; null if terminal. */
  deadline_at: string | null;
  opened_at: string;
  snapshot_url: string | null;
  tier_label: TierLabel;
}

export interface TimerSnapshotRow {
  incident_id: string;
  deadline_at: string | null;
  current_tier: number;
}

export interface TierAdvancedData {
  incident_id: string;
  current_tier: number;
  status: string;
  deadline_at: string | null;
}

export interface ResolvedData {
  incident_id: string;
  resolved_at: string;
  resolved_by: string | null;
}

/** Maps each WsType to its `data` payload. */
export interface WsData {
  snapshot: { incidents: IncidentView[] };
  "incident.created": IncidentView;
  "incident.updated": IncidentView;
  "incident.tier_advanced": TierAdvancedData;
  "incident.resolved": ResolvedData;
  "timer.snapshot": { incidents: TimerSnapshotRow[] };
  "system.heartbeat": Record<string, never>;
}

export interface WsEnvelope<T extends WsType = WsType> {
  type: T;
  version: number;
  seq: number;
  /** ISO-8601 UTC server clock at emit time. */
  server_now: string;
  data: WsData[T];
}

/** Discriminated union over all envelope variants (for exhaustive reducers). */
export type AnyWsEnvelope = {
  [T in WsType]: WsEnvelope<T>;
}[WsType];

export interface SubscribeMessage {
  action: "subscribe";
  topics: string[];
  last_seq: number;
}

/**
 * Returns true when the incoming sequence number indicates a FORWARD gap
 * (i.e. incoming > lastSeq + 1), meaning the client has missed messages
 * and should trigger a REST resync.
 *
 * Returns false for the next-in-sequence (incoming === lastSeq + 1),
 * out-of-order (incoming < lastSeq + 1), or duplicates.
 */
export function isForwardGap(lastSeq: number, incomingSeq: number): boolean {
  return incomingSeq > lastSeq + 1;
}
