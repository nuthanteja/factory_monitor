import { useEffect, useRef, useState } from "react";
import type { WebSocketLike, WsFactory } from "./useLiveIncidents";
import type { DetectionFrame } from "../lib/detectionContract";

export interface UseDetectionsOptions {
  wsFactory?: WsFactory;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  staleMs?: number;
}

function defaultWsUrl(cameraId: string): string {
  if (typeof window === "undefined") {
    return `ws://localhost/ws/detections/${cameraId}`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/detections/${cameraId}`;
}

const defaultFactory: WsFactory = (url) =>
  new WebSocket(url) as unknown as WebSocketLike;

/**
 * Subscribe to `/ws/detections/{cameraId}` and return the latest `DetectionFrame`.
 *
 * Latest-wins: every `detection.frame` message replaces the previous.
 * Staleness: if no frame arrives within `staleMs` (default 1000), returns null.
 * Fail-safe: WS down or parse error → null.  No subscribe message needed.
 */
export function useDetections(
  cameraId: string,
  opts: UseDetectionsOptions = {},
): DetectionFrame | null {
  const {
    wsFactory = defaultFactory,
    baseBackoffMs = 500,
    maxBackoffMs = 15000,
    staleMs = 1000,
  } = opts;

  const [frame, setFrame] = useState<DetectionFrame | null>(null);

  const socketRef = useRef<WebSocketLike | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const staleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef<number>(0);
  const closedByUsRef = useRef<boolean>(false);

  useEffect(() => {
    closedByUsRef.current = false;

    const clearStaleTimer = () => {
      if (staleTimerRef.current !== null) {
        clearTimeout(staleTimerRef.current);
        staleTimerRef.current = null;
      }
    };

    const armStaleTimer = () => {
      clearStaleTimer();
      staleTimerRef.current = setTimeout(() => {
        setFrame(null);
      }, staleMs);
    };

    const connect = () => {
      const url = defaultWsUrl(cameraId);
      const ws = wsFactory(url);
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        // No subscribe message — camera_id is in the URL
      };

      ws.onmessage = (ev) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(ev.data) as unknown;
        } catch {
          return; // malformed — ignore
        }
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          (parsed as Record<string, unknown>)["type"] !== "detection.frame"
        ) {
          return; // not a detection.frame envelope — ignore
        }
        const env = parsed as { type: string; data: DetectionFrame };
        setFrame(env.data);
        armStaleTimer();
      };

      const scheduleReconnect = () => {
        setFrame(null);
        clearStaleTimer();
        if (closedByUsRef.current) {
          return;
        }
        if (timerRef.current) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
        const delay = Math.min(
          maxBackoffMs,
          baseBackoffMs * 2 ** attemptRef.current,
        );
        attemptRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      };

      let reconnectScheduledByError = false;

      ws.onerror = () => {
        reconnectScheduledByError = true;
        scheduleReconnect();
        try {
          ws.close();
        } catch {
          /* noop */
        }
      };

      ws.onclose = (ev) => {
        if (reconnectScheduledByError) {
          return;
        }
        scheduleReconnect();
        void ev;
      };
    };

    connect();

    return () => {
      closedByUsRef.current = true;
      clearStaleTimer();
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      const ws = socketRef.current;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
      socketRef.current = null;
    };
  }, [cameraId, wsFactory, baseBackoffMs, maxBackoffMs, staleMs]);

  return frame;
}
