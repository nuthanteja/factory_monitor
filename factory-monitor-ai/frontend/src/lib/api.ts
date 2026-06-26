import type { Zone, HeatCell } from "./heatmapContract";
export type { Zone, HeatCell };

export type Severity = "low" | "medium" | "high" | "critical";

// ---------------------------------------------------------------------------
// Cameras
// ---------------------------------------------------------------------------

export interface Camera {
  id: string;
  name: string;
  whep_url: string | null;
  zone_id: string | null;
}

const CAMERAS_URL = "/api/v1/cameras";

export async function getCameras(signal?: AbortSignal): Promise<Camera[]> {
  const res = await fetch(CAMERAS_URL, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`getCameras failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as { cameras: Camera[] };
  return body.cameras;
}

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

// ---------------------------------------------------------------------------
// Zones
// ---------------------------------------------------------------------------

const ZONES_URL = "/api/v1/zones";

export async function getZones(signal?: AbortSignal): Promise<Zone[]> {
  const res = await fetch(ZONES_URL, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`getZones failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as { zones: Zone[] };
  return body.zones;
}

// ---------------------------------------------------------------------------
// Heatmap (REST seed — returns a flat HeatCell[] with camera_id attached)
// ---------------------------------------------------------------------------

const HEATMAP_URL = "/api/v1/heatmap";

type HeatmapApiResponse = {
  cameras: { camera_id: string; cells: { zone_id: string; count: number; ts: number | string }[] }[];
  meta: Record<string, unknown>;
};

export async function getHeatmap(signal?: AbortSignal): Promise<HeatCell[]> {
  const res = await fetch(HEATMAP_URL, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`getHeatmap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as HeatmapApiResponse;
  const cells: HeatCell[] = [];
  for (const cam of body.cameras) {
    for (const cell of cam.cells) {
      cells.push({ camera_id: cam.camera_id, zone_id: cell.zone_id, count: cell.count, ts: cell.ts });
    }
  }
  return cells;
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
