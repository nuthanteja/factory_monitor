import { http, HttpResponse } from "msw";
import type { Camera, IncidentsResponse } from "../../src/lib/api";

export const seededIncidents: IncidentsResponse = {
  incidents: [
    {
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
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      camera_id: "cam_02",
      zone_id: null,
      anomaly_type: "zone_intrusion",
      rule_id: "ZONE_INTRUSION",
      severity: "critical",
      object_class: null,
      status: "AWAITING_OPERATOR",
      current_tier: 0,
      deadline_at: null,
      tier_label: "Operator",
      created_at: "2026-06-22T10:16:00.000Z",
      snapshot_url: null,
    },
  ],
  meta: { server_now: "2026-06-22T10:16:05.000Z" },
};

export const seededCameras: Camera[] = [
  {
    id: "cam_01",
    name: "Weld Bay A",
    whep_url: "/whep/cam_01/whep",
    zone_id: "zone_weld_bay",
  },
  {
    id: "cam_02",
    name: "Assembly Line 1",
    whep_url: "/whep/cam_02/whep",
    zone_id: null,
  },
];

export const handlers = [
  http.get("/api/v1/incidents", () => HttpResponse.json(seededIncidents)),
  http.get("/api/v1/cameras", () => HttpResponse.json(seededCameras)),
];
