import { useEffect } from "react";
import type { RefObject } from "react";
import type { DetectionFrame } from "../lib/detectionContract";
import { drawDetections } from "../lib/detectionDraw";

export interface UseDetectionOverlayOptions {
  videoRef: RefObject<HTMLVideoElement>;
  canvasRef: RefObject<HTMLCanvasElement>;
  frame: DetectionFrame | null;
  active: boolean;
}

/**
 * Size the canvas backing store to match the video element (DPR-scaled) and
 * draw detection boxes when `active`.  Inert if getContext("2d") returns null
 * (e.g. happy-dom in tests).
 */
export function useDetectionOverlay({
  videoRef,
  canvasRef,
  frame,
  active,
}: UseDetectionOverlayOptions): void {
  useEffect(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return; // inert in happy-dom / unsupported env

    const dpr = typeof devicePixelRatio !== "undefined" ? devicePixelRatio : 1;
    const bw = Math.round(video.clientWidth * dpr);
    const bh = Math.round(video.clientHeight * dpr);

    if (bw > 0 && bh > 0) {
      canvas.width = bw;
      canvas.height = bh;
    }

    if (active) {
      drawDetections(ctx, frame, canvas.width, canvas.height, "cover");
    } else {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }, [frame, active, videoRef, canvasRef]);
}
