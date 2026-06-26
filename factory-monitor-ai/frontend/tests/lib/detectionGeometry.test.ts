import { describe, it, expect } from "vitest";
import { frameToCanvas } from "../../src/lib/detectionGeometry";
import type { DetectionBox } from "../../src/lib/detectionContract";

function box(x: number, y: number, w: number, h: number): DetectionBox {
  return { cls: "person", bbox: [x, y, w, h], track_id: 1, no_hardhat: false };
}

describe("frameToCanvas — cover", () => {
  it("wider canvas: scale by width, centres vertically", () => {
    // frame 100x100, canvas 200x100 → scale = max(2, 1) = 2
    // dx = (200 - 100*2)/2 = 0, dy = (100 - 100*2)/2 = -50
    const r = frameToCanvas(box(10, 10, 20, 20), { w: 100, h: 100 }, { w: 200, h: 100 });
    expect(r.x).toBeCloseTo(10 * 2 + 0);    // 20
    expect(r.y).toBeCloseTo(10 * 2 + -50);  // -30
    expect(r.w).toBeCloseTo(20 * 2);         // 40
    expect(r.h).toBeCloseTo(20 * 2);         // 40
  });

  it("taller canvas: scale by height, centres horizontally", () => {
    // frame 100x100, canvas 100x200 → scale = max(1, 2) = 2
    // dx = (100 - 100*2)/2 = -50, dy = (200 - 100*2)/2 = 0
    const r = frameToCanvas(box(10, 10, 20, 20), { w: 100, h: 100 }, { w: 100, h: 200 });
    expect(r.x).toBeCloseTo(10 * 2 + -50); // -30
    expect(r.y).toBeCloseTo(10 * 2 + 0);   // 20
    expect(r.w).toBeCloseTo(40);
    expect(r.h).toBeCloseTo(40);
  });

  it("identity: frame same as canvas, box unchanged", () => {
    const r = frameToCanvas(box(10, 20, 30, 40), { w: 640, h: 480 }, { w: 640, h: 480 });
    expect(r.x).toBeCloseTo(10);
    expect(r.y).toBeCloseTo(20);
    expect(r.w).toBeCloseTo(30);
    expect(r.h).toBeCloseTo(40);
  });

  it("centre-invariance: a box at the centre of the frame maps to centre of canvas", () => {
    // frame 200x100, canvas 400x100 → scale = max(2, 1) = 2, dx=0, dy=(100-200)/2=-50
    // centre box at (100, 50) with w=0 h=0
    const r = frameToCanvas(box(100, 50, 0, 0), { w: 200, h: 100 }, { w: 400, h: 100 });
    // x = 100*2 + 0 = 200 = cw/2 ✓
    expect(r.x).toBeCloseTo(200);
    // y = 50*2 + -50 = 50 ≠ canvas centre (50) — with cover clipped top+bottom
    // After cover: y = 50*2 - 50 = 50 which equals ch/2 = 50 ✓
    expect(r.y).toBeCloseTo(50);
  });
});

describe("frameToCanvas — fill", () => {
  it("scales x and y independently", () => {
    // frame 200x100, canvas 400x200 → sx=2, sy=2 → same as uniform here
    const r = frameToCanvas(box(10, 10, 20, 20), { w: 200, h: 100 }, { w: 400, h: 200 }, "fill");
    expect(r.x).toBeCloseTo(20);
    expect(r.y).toBeCloseTo(20);
    expect(r.w).toBeCloseTo(40);
    expect(r.h).toBeCloseTo(40);
  });

  it("non-uniform: different x and y scales, no offset", () => {
    // frame 100x100, canvas 300x100 → sx=3, sy=1
    const r = frameToCanvas(box(10, 10, 20, 20), { w: 100, h: 100 }, { w: 300, h: 100 }, "fill");
    expect(r.x).toBeCloseTo(30);
    expect(r.y).toBeCloseTo(10);
    expect(r.w).toBeCloseTo(60);
    expect(r.h).toBeCloseTo(20);
  });
});

describe("frameToCanvas — contain", () => {
  it("wider canvas: scale by height (min), centres horizontally", () => {
    // frame 100x100, canvas 200x100 → scale = min(2,1) = 1
    // dx = (200 - 100*1)/2 = 50, dy = (100 - 100*1)/2 = 0
    const r = frameToCanvas(box(10, 10, 20, 20), { w: 100, h: 100 }, { w: 200, h: 100 }, "contain");
    expect(r.x).toBeCloseTo(10 * 1 + 50); // 60
    expect(r.y).toBeCloseTo(10 * 1 + 0);  // 10
    expect(r.w).toBeCloseTo(20);
    expect(r.h).toBeCloseTo(20);
  });
});

describe("frameToCanvas — degenerate", () => {
  it("zero frame width → {0,0,0,0}, no NaN", () => {
    const r = frameToCanvas(box(10, 10, 5, 5), { w: 0, h: 100 }, { w: 640, h: 480 });
    expect(r).toEqual({ x: 0, y: 0, w: 0, h: 0 });
    expect(Object.values(r).some(Number.isNaN)).toBe(false);
  });

  it("zero frame height → {0,0,0,0}, no NaN", () => {
    const r = frameToCanvas(box(10, 10, 5, 5), { w: 100, h: 0 }, { w: 640, h: 480 });
    expect(r).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });

  it("zero canvas width → {0,0,0,0}, no NaN", () => {
    const r = frameToCanvas(box(10, 10, 5, 5), { w: 100, h: 100 }, { w: 0, h: 480 });
    expect(r).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });

  it("negative frame dim → {0,0,0,0}, no NaN", () => {
    const r = frameToCanvas(box(10, 10, 5, 5), { w: -1, h: 100 }, { w: 640, h: 480 });
    expect(r).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });
});
