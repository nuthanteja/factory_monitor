import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  applyEnvelope,
  initialLiveState,
  selectSortedIncidents,
  type LiveState,
} from "../lib/liveReducer";
import { ServerClock, WS_TOPICS } from "../lib/serverClock";
import type {
  AnyWsEnvelope,
  IncidentView,
  SubscribeMessage,
} from "../lib/wsContract";
import { INCIDENTS_QUERY_KEY } from "./useIncidents";

export interface WebSocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((ev?: unknown) => void) | null;
  onmessage: ((ev: { data: string }) => void) | null;
  onclose: ((ev?: unknown) => void) | null;
  onerror: ((ev?: unknown) => void) | null;
}

export type WsFactory = (url: string) => WebSocketLike;

export interface UseLiveIncidentsOptions {
  url?: string;
  wsFactory?: WsFactory;
  clock?: ServerClock;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
}

export interface LiveIncidentsResult {
  incidents: IncidentView[];
  connected: boolean;
  clock: ServerClock;
  lastServerNowIso: string | null;
}

function defaultWsUrl(): string {
  if (typeof window === "undefined") {
    return "ws://localhost/ws/live";
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/live`;
}

const defaultFactory: WsFactory = (url) =>
  new WebSocket(url) as unknown as WebSocketLike;

export function useLiveIncidents(
  opts: UseLiveIncidentsOptions = {},
): LiveIncidentsResult {
  const {
    url = defaultWsUrl(),
    wsFactory = defaultFactory,
    baseBackoffMs = 500,
    maxBackoffMs = 15000,
  } = opts;

  const queryClient = useQueryClient();
  const clockRef = useRef<ServerClock>(opts.clock ?? new ServerClock());
  const stateRef = useRef<LiveState>(initialLiveState);
  const lastSeqRef = useRef<number>(0);
  const attemptRef = useRef<number>(0);
  const closedByUsRef = useRef<boolean>(false);
  const socketRef = useRef<WebSocketLike | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [incidents, setIncidents] = useState<IncidentView[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastServerNowIso, setLastServerNowIso] = useState<string | null>(null);

  useEffect(() => {
    closedByUsRef.current = false;

    const connect = () => {
      const ws = wsFactory(url);
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
        const sub: SubscribeMessage = {
          action: "subscribe",
          topics: [...WS_TOPICS],
          last_seq: lastSeqRef.current,
        };
        ws.send(JSON.stringify(sub));
      };

      ws.onmessage = (ev) => {
        let env: AnyWsEnvelope;
        try {
          env = JSON.parse(ev.data) as AnyWsEnvelope;
        } catch {
          return;
        }
        const res = applyEnvelope(stateRef.current, env);
        if (!res.applied) {
          return;
        }
        stateRef.current = res.state;
        lastSeqRef.current = res.state.lastSeq;
        clockRef.current.update(env.server_now);
        setIncidents(selectSortedIncidents(res.state));
        setLastServerNowIso(res.state.lastServerNowIso);
        if (res.gap) {
          void queryClient.invalidateQueries({ queryKey: INCIDENTS_QUERY_KEY });
        }
      };

      const scheduleReconnect = () => {
        setConnected(false);
        if (closedByUsRef.current) {
          return;
        }
        const delay = Math.min(
          maxBackoffMs,
          baseBackoffMs * 2 ** attemptRef.current,
        );
        attemptRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      };

      ws.onclose = scheduleReconnect;
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* noop */
        }
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
  }, [url, wsFactory, baseBackoffMs, maxBackoffMs, queryClient]);

  return {
    incidents,
    connected,
    clock: clockRef.current,
    lastServerNowIso,
  };
}
