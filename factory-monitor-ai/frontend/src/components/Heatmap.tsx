import { useZones } from "../hooks/useZones";
import { useHeatmap, type UseHeatmapOptions } from "../hooks/useHeatmap";
import { scaleMax } from "../lib/heatmapColor";
import { ZoneMap } from "./ZoneMap";
import type { Zone } from "../lib/api";

export function Heatmap(opts: UseHeatmapOptions = {}): JSX.Element {
  const zonesQuery = useZones();
  const { cells, connected } = useHeatmap(opts);

  if (zonesQuery.isLoading) {
    return (
      <p data-testid="heatmap-loading" role="status">
        Loading zone map…
      </p>
    );
  }

  if (zonesQuery.isError) {
    return (
      <p data-testid="heatmap-error" role="alert">
        Failed to load zones: {zonesQuery.error?.message ?? "unknown error"}
      </p>
    );
  }

  const zones = zonesQuery.data ?? [];

  if (zones.length === 0) {
    return (
      <p data-testid="heatmap-empty" role="status">
        No zones configured.
      </p>
    );
  }

  // Compute observed max across all current cells.
  const observedMax = cells.reduce((m, c) => Math.max(m, c.count), 0);
  const maxVal = scaleMax(observedMax);

  // Group zones by camera_id.
  const zonesByCamera = new Map<string, Zone[]>();
  for (const zone of zones) {
    const list = zonesByCamera.get(zone.camera_id) ?? [];
    list.push(zone);
    zonesByCamera.set(zone.camera_id, list);
  }

  return (
    <section data-testid="heatmap-wall">
      {/* Connection status pill */}
      <span
        data-testid="heatmap-connection-pill"
        data-connected={connected}
        role="status"
        style={{ marginBottom: "0.5rem", display: "inline-block" }}
      >
        {connected ? "LIVE" : "RECONNECTING…"}
      </span>

      {/* Colour legend */}
      <div
        data-testid="heatmap-legend"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          marginBottom: "0.75rem",
          fontSize: "0.75rem",
        }}
      >
        <span>0</span>
        <div
          style={{
            width: 120,
            height: 10,
            background:
              "linear-gradient(to right, hsla(210,85%,50%,0.25), hsla(0,85%,50%,0.85))",
            borderRadius: 2,
          }}
        />
        <span>{maxVal}+</span>
      </div>

      {/* Per-camera SVG grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: "0.75rem",
          padding: "0.5rem",
        }}
      >
        {[...zonesByCamera.entries()].map(([cameraId, cameraZones]) => (
          <div
            key={cameraId}
            data-testid="heatmap-camera-tile"
            style={{
              border: "1px solid rgba(255,255,255,0.15)",
              borderRadius: 6,
              padding: "0.5rem",
              background: "rgba(0,0,0,0.3)",
            }}
          >
            <div style={{ fontSize: "0.75rem", marginBottom: "0.25rem", opacity: 0.7 }}>
              {cameraId}
            </div>
            <ZoneMap
              cameraId={cameraId}
              zones={cameraZones}
              cells={cells}
              scaleMaxVal={maxVal}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
