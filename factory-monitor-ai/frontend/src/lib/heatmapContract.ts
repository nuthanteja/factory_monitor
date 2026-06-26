export interface Zone {
  id: string;
  camera_id: string;
  name: string;
  polygon: [number, number][];
}

export interface HeatCell {
  camera_id: string;
  zone_id: string;
  count: number;
  ts: number | string;
}

export interface HeatmapTick {
  type: "heatmap.tick";
  data: {
    camera_id: string;
    cells: { zone_id: string; count: number }[];
    ts: number | string;
  };
}
