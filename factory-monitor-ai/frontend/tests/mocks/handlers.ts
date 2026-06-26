import { http, HttpResponse } from "msw";
import type { Camera, IncidentsResponse, Zone } from "../../src/lib/api";

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

export const seededZones: Zone[] = [
  {
    id: "zone_weld_bay",
    camera_id: "cam_01",
    name: "Weld Bay",
    polygon: [
      [10, 10],
      [90, 10],
      [90, 90],
      [10, 90],
    ],
  },
  {
    id: "zone_entry",
    camera_id: "cam_01",
    name: "Entry",
    polygon: [
      [100, 10],
      [180, 10],
      [180, 90],
      [100, 90],
    ],
  },
  {
    id: "zone_assembly",
    camera_id: "cam_02",
    name: "Assembly",
    polygon: [
      [10, 10],
      [90, 10],
      [90, 90],
      [10, 90],
    ],
  },
];

export const seededHeatmapResponse = {
  cameras: [
    {
      camera_id: "cam_01",
      cells: [
        { zone_id: "zone_weld_bay", count: 3, ts: 1719050000 },
        { zone_id: "zone_entry", count: 0, ts: 1719050000 },
      ],
    },
    {
      camera_id: "cam_02",
      cells: [{ zone_id: "zone_assembly", count: 5, ts: 1719050000 }],
    },
  ],
  meta: {},
};

export const handlers = [
  http.get("/api/v1/incidents", () => HttpResponse.json(seededIncidents)),
  http.get("/api/v1/cameras", () => HttpResponse.json({ cameras: seededCameras })),
  http.get("/api/v1/zones", () => HttpResponse.json({ zones: seededZones })),
  http.get("/api/v1/heatmap", () => HttpResponse.json(seededHeatmapResponse)),
];
