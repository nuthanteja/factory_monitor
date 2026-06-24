export type Severity = "low" | "medium" | "high" | "critical";

export interface Incident {
  id: string;
  camera_id: string;
  zone_id: string | null;
  anomaly_type: string;
  rule_id: string;
  severity: Severity;
  object_class: string | null;
  status: string;
  current_tier: number;
  deadline_at: string | null;
  tier_label: string;
  created_at: string;
  snapshot_url: string | null;
}

export interface IncidentsResponse {
  incidents: Incident[];
  meta: { server_now: string };
}

const INCIDENTS_URL = "/api/v1/incidents";

export async function getIncidents(
  signal?: AbortSignal,
): Promise<IncidentsResponse> {
  const res = await fetch(INCIDENTS_URL, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`getIncidents failed: HTTP ${res.status}`);
  }
  return (await res.json()) as IncidentsResponse;
}
