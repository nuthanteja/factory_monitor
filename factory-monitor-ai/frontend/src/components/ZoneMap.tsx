import { countToColor } from "../lib/heatmapColor";
import type { Zone, HeatCell } from "../lib/api";

export interface ZoneMapProps {
  cameraId: string;
  zones: Zone[];
  cells: HeatCell[];
  scaleMaxVal: number;
}

function centroid(polygon: [number, number][]): [number, number] {
  if (polygon.length === 0) return [0, 0];
  let cx = 0;
  let cy = 0;
  for (const [x, y] of polygon) {
    cx += x;
    cy += y;
  }
  return [cx / polygon.length, cy / polygon.length];
}

function pointsAttr(polygon: [number, number][]): string {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}

export function ZoneMap({ cameraId, zones, cells, scaleMaxVal }: ZoneMapProps): JSX.Element {
  // Build count lookup keyed by zone_id for this camera.
  const countByZone = new Map<string, number>();
  for (const cell of cells) {
    if (cell.camera_id === cameraId) {
      countByZone.set(cell.zone_id, cell.count);
    }
  }

  // Compute the union bounding box of all polygons.
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let hasVertices = false;

  for (const zone of zones) {
    for (const [x, y] of zone.polygon) {
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      hasVertices = true;
    }
  }

  // Fallback viewBox for degenerate / empty polygons.
  const PADDING = 10;
  let viewBox: string;
  if (
    !hasVertices ||
    !isFinite(minX) ||
    !isFinite(minY) ||
    maxX - minX < 1 ||
    maxY - minY < 1
  ) {
    viewBox = "0 0 100 100";
  } else {
    const vx = minX - PADDING;
    const vy = minY - PADDING;
    const vw = maxX - minX + PADDING * 2;
    const vh = maxY - minY + PADDING * 2;
    viewBox = `${vx} ${vy} ${vw} ${vh}`;
  }

  return (
    <svg
      data-testid="zone-map"
      data-camera-id={cameraId}
      viewBox={viewBox}
      preserveAspectRatio="xMidYMid meet"
      style={{ width: "100%", height: "100%", minHeight: 160 }}
    >
      {zones.map((zone) => {
        if (zone.polygon.length < 3) return null; // skip degenerate polygons
        const count = countByZone.get(zone.id) ?? 0;
        const fill = countToColor(count, scaleMaxVal);
        const [cx, cy] = centroid(zone.polygon);
        return (
          <g key={zone.id}>
            <polygon
              data-testid="zone-region"
              data-zone-id={zone.id}
              data-count={count}
              points={pointsAttr(zone.polygon)}
              fill={fill}
              stroke="rgba(255,255,255,0.4)"
              strokeWidth={1}
            />
            <text
              x={cx}
              y={cy}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={8}
              fill="white"
              style={{ pointerEvents: "none", userSelect: "none" }}
            >
              {count}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
