import type { DetectionFrame } from "./detectionContract";
import { frameToCanvas, type FitMode } from "./detectionGeometry";

const COLOR_NO_HARDHAT = "#ef4444"; // red
const COLOR_OK = "#22d3ee"; // cyan

/**
 * Draw the latest detection boxes onto the canvas context.
 *
 * Always clears first. If `frame` is null or has zero/negative dims, only
 * the clear is performed (fail-safe: no stale boxes on screen).
 */
export function drawDetections(
  ctx: CanvasRenderingContext2D,
  frame: DetectionFrame | null,
  cw: number,
  ch: number,
  fit: FitMode = "cover",
): void {
  ctx.clearRect(0, 0, cw, ch);

  if (!frame || frame.frame_w <= 0 || frame.frame_h <= 0) {
    return;
  }

  const frameDims = { w: frame.frame_w, h: frame.frame_h };
  const canvasDims = { w: cw, h: ch };

  for (const box of frame.boxes) {
    const rect = frameToCanvas(box, frameDims, canvasDims, fit);
    const color = box.no_hardhat ? COLOR_NO_HARDHAT : COLOR_OK;

    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;

    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    ctx.font = "12px sans-serif";
    ctx.fillText(`${box.cls} #${box.track_id}`, rect.x, rect.y - 4);
  }
}
