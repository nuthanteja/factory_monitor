import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getHeatmap, type HeatCell } from "../lib/api";
import type { HeatmapTick } from "../lib/heatmapContract";
import type { WebSocketLike, WsFactory } from "./useLiveIncidents";

export const HEATMAP_QUERY_KEY = ["heatmap"] as const;

export interface UseHeatmapOptions {
  wsUrl?: string;
  wsFactory?: WsFactory;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
}

export interface UseHeatmapResult {
  cells: HeatCell[];
  connected: boolean;
}

function defaultWsUrl(): string {
  if (typeof window === "undefined") {
    return "ws://localhost/ws/heatmap";
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/heatmap`;
}

const defaultFactory: WsFactory = (url) =>
  new WebSocket(url) as unknown as WebSocketLike;

export function useHeatmap(opts: UseHeatmapOptions = {}): UseHeatmapResult {
  const {
    wsUrl = defaultWsUrl(),
    wsFactory = defaultFactory,
    baseBackoffMs = 500,
    maxBackoffMs = 15_000,
  } = opts;

  // REST seed — provides initial data before first WS tick.
  const restQuery = useQuery<HeatCell[], Error>({
    queryKey: HEATMAP_QUERY_KEY,
    queryFn: ({ signal }) => getHeatmap(signal),
    staleTime: Infinity, // counts persist; WS keeps them live
  });

  // Map of "camera_id::zone_id" → HeatCell — latest-wins merge.
  const cellMapRef = useRef<Map<string, HeatCell>>(new Map());
  // Whether we've received at least one WS tick (so we stop returning REST data).
  const wsReadyRef = useRef(false);

  const [liveCells, setLiveCells] = useState<HeatCell[]>([]);
  const [connected, setConnected] = useState(false);

  const attemptRef = useRef(0);
  const closedByUsRef = useRef(false);
  const socketRef = useRef<WebSocketLike | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    closedByUsRef.current = false;

    const connect = () => {
      const ws = wsFactory(wsUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
      };

      ws.onmessage = (ev) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(ev.data);
        } catch {
          return; // malformed JSON — ignore
        }

        // Type-guard: must be a heatmap.tick envelope.
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          (parsed as Record<string, unknown>)["type"] !== "heatmap.tick"
        ) {
          return;
        }

        const tick = parsed as HeatmapTick;
        if (
          !tick.data ||
          typeof tick.data.camera_id !== "string" ||
          !Array.isArray(tick.data.cells)
        ) {
          return; // malformed tick body — ignore
        }

        // Seed the map with REST data on first tick (so camera-A data survives
        // a camera-B tick that doesn't mention camera-A).
        if (!wsReadyRef.current && restQuery.data) {
          for (const cell of restQuery.data) {
            cellMapRef.current.set(`${cell.camera_id}::${cell.zone_id}`, cell);
          }
        }
        wsReadyRef.current = true;

        const ts = tick.data.ts;
        for (const c of tick.data.cells) {
          const key = `${tick.data.camera_id}::${c.zone_id}`;
          cellMapRef.current.set(key, {
            camera_id: tick.data.camera_id,
            zone_id: c.zone_id,
            count: c.count,
            ts,
          });
        }

        setLiveCells([...cellMapRef.current.values()]);
      };

      const scheduleReconnect = () => {
        setConnected(false);
        if (closedByUsRef.current) return;
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
        if (reconnectScheduledByError) return;
        scheduleReconnect();
        void ev; // satisfies lint
      };
    };

    connect();

    return () => {
      closedByUsRef.current = true;
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
  }, [wsUrl, wsFactory, baseBackoffMs, maxBackoffMs, restQuery.data]);

  // Before any WS tick: return REST seed. After first tick: return live cells.
  const cells = wsReadyRef.current ? liveCells : (restQuery.data ?? []);

  return { cells, connected };
}
