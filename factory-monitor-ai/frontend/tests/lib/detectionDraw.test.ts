import { describe, it, expect, vi, beforeEach } from "vitest";
import { drawDetections } from "../../src/lib/detectionDraw";
import { frameToCanvas } from "../../src/lib/detectionGeometry";
import type { DetectionFrame, DetectionBox } from "../../src/lib/detectionContract";

// --- spy ctx factory ---
function makeCtx() {
  const ctx = {
    strokeStyle: "" as string,
    fillStyle: "" as string,
    lineWidth: 0,
    font: "",
    clearRect: vi.fn<[number, number, number, number], void>(),
    strokeRect: vi.fn<[number, number, number, number], void>(),
    fillText: vi.fn<[string, number, number], void>(),
  };
  return ctx as unknown as CanvasRenderingContext2D & typeof ctx;
}

function makeBox(over: Partial<DetectionBox> = {}): DetectionBox {
  return {
    cls: "person",
    bbox: [10, 20, 30, 40],
    track_id: 7,
    no_hardhat: false,
    ...over,
  };
}

function makeFrame(boxes: DetectionBox[]): DetectionFrame {
  return { camera_id: "cam_01", ts: 0, frame_w: 640, frame_h: 480, seq: 1, boxes };
}

describe("drawDetections", () => {
  let ctx: ReturnType<typeof makeCtx>;

  beforeEach(() => {
    ctx = makeCtx();
  });

  it("always calls clearRect(0,0,cw,ch)", () => {
    drawDetections(ctx, null, 320, 240);
    expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 320, 240);
  });

  it("frame=null → only clearRect, no strokeRect/fillText", () => {
    drawDetections(ctx, null, 320, 240);
    expect(ctx.clearRect).toHaveBeenCalledTimes(1);
    expect(ctx.strokeRect).not.toHaveBeenCalled();
    expect(ctx.fillText).not.toHaveBeenCalled();
  });

  it("frame with zero frame_w → only clearRect", () => {
    const frame: DetectionFrame = { ...makeFrame([makeBox()]), frame_w: 0 };
    drawDetections(ctx, frame, 320, 240);
    expect(ctx.strokeRect).not.toHaveBeenCalled();
  });

  it("draws strokeRect at coords from frameToCanvas", () => {
    const box = makeBox({ bbox: [10, 20, 30, 40] });
    const frame = makeFrame([box]);
    const cw = 320;
    const ch = 240;
    drawDetections(ctx, frame, cw, ch, "cover");

    const expected = frameToCanvas(box, { w: 640, h: 480 }, { w: cw, h: ch }, "cover");
    expect(ctx.strokeRect).toHaveBeenCalledWith(
      expected.x,
      expected.y,
      expected.w,
      expected.h,
    );
  });

  it("uses red (#ef4444) when no_hardhat=true", () => {
    const box = makeBox({ no_hardhat: true });
    const frame = makeFrame([box]);

    let capturedStroke = "";
    let capturedFill = "";
    const spyCtx = {
      ...ctx,
      clearRect: vi.fn(),
      strokeRect: vi.fn(() => {
        capturedStroke = (spyCtx as unknown as Record<string, string>).strokeStyle;
        capturedFill = (spyCtx as unknown as Record<string, string>).fillStyle;
      }),
      fillText: vi.fn(),
    };
    drawDetections(spyCtx as unknown as CanvasRenderingContext2D, frame, 320, 240);
    expect(capturedStroke).toBe("#ef4444");
    expect(capturedFill).toBe("#ef4444");
  });

  it("uses cyan (#22d3ee) when no_hardhat=false", () => {
    const box = makeBox({ no_hardhat: false });
    const frame = makeFrame([box]);

    let capturedStroke = "";
    const spyCtx = {
      ...ctx,
      clearRect: vi.fn(),
      strokeRect: vi.fn(() => {
        capturedStroke = (spyCtx as unknown as Record<string, string>).strokeStyle;
      }),
      fillText: vi.fn(),
    };
    drawDetections(spyCtx as unknown as CanvasRenderingContext2D, frame, 320, 240);
    expect(capturedStroke).toBe("#22d3ee");
  });

  it("fillText includes cls and track_id", () => {
    const box = makeBox({ cls: "person", track_id: 42 });
    const frame = makeFrame([box]);
    drawDetections(ctx, frame, 320, 240);

    expect(ctx.fillText).toHaveBeenCalledTimes(1);
    const [label] = ctx.fillText.mock.calls[0];
    expect(label).toContain("person");
    expect(label).toContain("42");
  });

  it("draws multiple boxes", () => {
    const boxes = [
      makeBox({ track_id: 1 }),
      makeBox({ track_id: 2, no_hardhat: true }),
    ];
    const frame = makeFrame(boxes);
    drawDetections(ctx, frame, 320, 240);
    expect(ctx.strokeRect).toHaveBeenCalledTimes(2);
    expect(ctx.fillText).toHaveBeenCalledTimes(2);
  });
});
