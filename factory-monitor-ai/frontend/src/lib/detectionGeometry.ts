import type { DetectionBox } from "./detectionContract";

export type FitMode = "cover" | "fill" | "contain";

export interface CanvasRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Dims {
  w: number;
  h: number;
}

/**
 * Map a bounding box from frame coordinates to canvas coordinates.
 *
 * cover  — scale = max(cw/fw, ch/fh); centred with letterbox offsets.
 * fill   — independent per-axis scale, no offsets.
 * contain — scale = min(cw/fw, ch/fh); centred with letterbox offsets.
 *
 * Degenerate (any zero / negative dimension) → {0,0,0,0}  (no NaN).
 */
export function frameToCanvas(
  box: DetectionBox,
  frame: Dims,
  canvas: Dims,
  fit: FitMode = "cover",
): CanvasRect {
  const { w: fw, h: fh } = frame;
  const { w: cw, h: ch } = canvas;

  // Guard degenerate dims — avoids NaN / Infinity
  if (fw <= 0 || fh <= 0 || cw <= 0 || ch <= 0) {
    return { x: 0, y: 0, w: 0, h: 0 };
  }

  const [bx, by, bw, bh] = box.bbox;

  if (fit === "fill") {
    const sx = cw / fw;
    const sy = ch / fh;
    return { x: bx * sx, y: by * sy, w: bw * sx, h: bh * sy };
  }

  const scale =
    fit === "cover"
      ? Math.max(cw / fw, ch / fh)
      : Math.min(cw / fw, ch / fh); // contain

  const dx = (cw - fw * scale) / 2;
  const dy = (ch - fh * scale) / 2;

  return {
    x: bx * scale + dx,
    y: by * scale + dy,
    w: bw * scale,
    h: bh * scale,
  };
}
