export const MIN_SCALE_MAX = 4;

/**
 * Derive the colour-scale ceiling from the highest observed count.
 * Always at least MIN_SCALE_MAX so sparse zones don't span the full hue range
 * on first arrival.
 */
export function scaleMax(observed: number): number {
  return Math.max(MIN_SCALE_MAX, Math.floor(observed) || 0);
}

/**
 * Map a raw person-count to an HSLA fill colour.
 *
 * - count <= 0 or non-finite  → fully transparent
 * - t = count / max  (clamped to [0, 1])
 * - hue:   210 (cool blue) at t=0  →  0 (hot red) at t=1
 * - alpha: 0.25 at t=0  →  0.85 at t=1
 */
export function countToColor(count: number, max: number): string {
  if (!isFinite(count) || count <= 0) {
    return "rgba(0,0,0,0)";
  }
  const safeMax = max > 0 ? max : 1;
  const t = Math.min(1, count / safeMax);
  const hue = Math.round(210 * (1 - t));
  const alpha = (0.25 + 0.6 * t).toFixed(2);
  return `hsla(${hue}, 85%, 50%, ${alpha})`;
}
