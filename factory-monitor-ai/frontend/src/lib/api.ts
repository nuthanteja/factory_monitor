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

export interface ActionResponse {
  incident_id: string;
  status: string;
}

function newIdempotencyKey(): string {
  return crypto.randomUUID();
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

export async function acknowledgeIncident(
  id: string,
  idempotencyKey: string = newIdempotencyKey(),
  signal?: AbortSignal,
): Promise<ActionResponse> {
  const res = await fetch(`${INCIDENTS_URL}/${id}/acknowledge`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    signal,
  });
  if (!res.ok) {
    throw new Error(`acknowledgeIncident failed: HTTP ${res.status}`);
  }
  return (await res.json()) as ActionResponse;
}

export async function resolveIncident(
  id: string,
  resolutionNote = "",
  idempotencyKey: string = newIdempotencyKey(),
  signal?: AbortSignal,
): Promise<ActionResponse> {
  const res = await fetch(`${INCIDENTS_URL}/${id}/resolve`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ resolution_note: resolutionNote }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`resolveIncident failed: HTTP ${res.status}`);
  }
  return (await res.json()) as ActionResponse;
}
