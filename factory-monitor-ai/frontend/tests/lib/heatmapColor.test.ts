import { describe, it, expect } from "vitest";
import { countToColor, scaleMax, MIN_SCALE_MAX } from "../../src/lib/heatmapColor";

const TRANSPARENT = "rgba(0,0,0,0)";

describe("countToColor", () => {
  it("returns transparent for count = 0", () => {
    expect(countToColor(0, 4)).toBe(TRANSPARENT);
  });

  it("returns transparent for negative count", () => {
    expect(countToColor(-1, 4)).toBe(TRANSPARENT);
  });

  it("returns transparent for NaN count", () => {
    expect(countToColor(NaN, 4)).toBe(TRANSPARENT);
  });

  it("returns transparent for Infinity count", () => {
    expect(countToColor(Infinity, 4)).toBe(TRANSPARENT);
  });

  it("returns transparent for -Infinity count", () => {
    expect(countToColor(-Infinity, 4)).toBe(TRANSPARENT);
  });

  it("returns hsla starting with hue=0 when count equals max (hot, full red)", () => {
    const color = countToColor(4, 4);
    expect(color).toMatch(/^hsla\(0,/);
  });

  it("returns a hue > 0 (cooler) for a mid-range count", () => {
    const color = countToColor(2, 4);
    // t = 0.5 → hue = round(210 * 0.5) = 105
    expect(color).toMatch(/^hsla\(105,/);
  });

  it("does not throw when max = 0 (guard: treats max as 1)", () => {
    expect(() => countToColor(1, 0)).not.toThrow();
    // count=1, max treated as 1 → t=1 → hue=0
    expect(countToColor(1, 0)).toMatch(/^hsla\(0,/);
  });

  it("clamps t at 1 when count > max", () => {
    const color = countToColor(10, 4);
    expect(color).toMatch(/^hsla\(0,/);
  });

  it("returns hsla format with 85% saturation and 50% lightness", () => {
    const color = countToColor(2, 4);
    expect(color).toContain("85%");
    expect(color).toContain("50%");
  });

  it("alpha is 0.25 at the minimum non-zero end (count=1, max=very large so t≈0)", () => {
    const color = countToColor(1, 1_000_000);
    // t ≈ 0 → alpha = (0.25 + 0.6*~0).toFixed(2) = "0.25"
    expect(color).toMatch(/0\.25\)$/);
  });

  it("alpha is 0.85 at the maximum end (t=1)", () => {
    const color = countToColor(4, 4);
    expect(color).toMatch(/0\.85\)$/);
  });
});

describe("scaleMax", () => {
  it("returns MIN_SCALE_MAX when observed is below it", () => {
    expect(scaleMax(0)).toBe(MIN_SCALE_MAX);
    expect(scaleMax(1)).toBe(MIN_SCALE_MAX);
    expect(scaleMax(3)).toBe(MIN_SCALE_MAX);
  });

  it("returns the floored observed value when it exceeds MIN_SCALE_MAX", () => {
    expect(scaleMax(5)).toBe(5);
    expect(scaleMax(5.9)).toBe(5); // Math.floor
    expect(scaleMax(10)).toBe(10);
  });

  it("handles MIN_SCALE_MAX exactly", () => {
    expect(scaleMax(MIN_SCALE_MAX)).toBe(MIN_SCALE_MAX);
  });

  it("floors fractional values", () => {
    expect(scaleMax(6.7)).toBe(6);
    expect(scaleMax(4.1)).toBe(MIN_SCALE_MAX); // floor(4.1)=4 = MIN_SCALE_MAX
  });

  it("is never less than MIN_SCALE_MAX", () => {
    for (const v of [0, 0.5, 1, 2, 3, 3.9]) {
      expect(scaleMax(v)).toBeGreaterThanOrEqual(MIN_SCALE_MAX);
    }
  });
});
