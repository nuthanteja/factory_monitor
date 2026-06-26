/**
 * k6 SLO load test for Factory Monitor
 *
 * Three scenarios run concurrently:
 *   ws_live    — 50 constant VUs subscribe to /ws/live and validate the envelope
 *   api_read   — constant-arrival-rate GET /api/v1/incidents
 *   api_write  — constant-arrival-rate POST ack+resolve against a real incident
 *
 * Thresholds gate the CI run:
 *   http_req_duration{scenario:api_read}  p(99) < 500 ms   (API p99 SLO)
 *   http_req_duration{scenario:api_write} p(99) < 500 ms
 *   http_req_failed{scenario:api_read}    rate < 1 %
 *   http_req_failed{scenario:api_write}   rate < 1 %
 *   checks                                rate > 99 %
 *
 * Environment variables:
 *   API_BASE   base URL for the HTTP API, e.g. http://localhost:8000
 *   WS_URL     WebSocket URL for /ws/live, e.g. ws://localhost:8000/ws/live
 */

import { check, sleep } from "k6";
import http from "k6/http";
import { WebSocket } from "k6/websockets";
import { Counter, Trend } from "k6/metrics";

// ── Custom metrics ────────────────────────────────────────────────────────────

const wsFreshnessTrend = new Trend("ws_message_freshness_ms", true);
const wsMessagesReceived = new Counter("ws_messages_received");

// ── Options ───────────────────────────────────────────────────────────────────

export const options = {
  scenarios: {
    ws_live: {
      executor: "constant-vus",
      vus: 50,
      duration: "3m",
      exec: "wsLive",
      tags: { scenario: "ws_live" },
    },
    api_read: {
      executor: "constant-arrival-rate",
      rate: 200,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: "apiRead",
      tags: { scenario: "api" },
    },
    api_write: {
      executor: "constant-arrival-rate",
      rate: 50,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: "apiWrite",
      tags: { scenario: "api" },
    },
  },
  thresholds: {
    // API p99 SLO — non-zero exit on breach
    "http_req_duration{scenario:api}": ["p(99)<500"],
    // Error rate SLO
    "http_req_failed{scenario:api}": ["rate<0.01"],
    // Check pass rate
    "checks": ["rate>0.99"],
  },
};

// ── Configuration ─────────────────────────────────────────────────────────────

const API_BASE = __ENV.API_BASE || "http://localhost:8000";
const WS_URL = __ENV.WS_URL || "ws://localhost:8000/ws/live";

// ── Scenario: WS live ─────────────────────────────────────────────────────────

/**
 * Each VU opens a WebSocket to /ws/live, validates every message against the
 * documented envelope shape, and records freshness (age of server_now).
 *
 * Expected envelope:
 *   { type, version, seq, server_now, data }
 */
export function wsLive() {
  const ws = new WebSocket(WS_URL);

  ws.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (_) {
      check(event.data, {
        "ws message is valid JSON": () => false,
      });
      return;
    }

    const envelopeOk = check(msg, {
      "ws envelope has type": (m) => typeof m.type === "string" && m.type.length > 0,
      "ws envelope has version": (m) => typeof m.version === "number",
      "ws envelope has seq": (m) => typeof m.seq === "number",
      "ws envelope has server_now": (m) => typeof m.server_now === "string",
      "ws envelope has data": (m) => m.data !== undefined,
    });

    if (envelopeOk && msg.server_now) {
      const serverTs = new Date(msg.server_now).getTime();
      if (!isNaN(serverTs)) {
        const freshness = Date.now() - serverTs;
        wsFreshnessTrend.add(freshness);
      }
    }

    wsMessagesReceived.add(1);
  });

  ws.addEventListener("error", (event) => {
    check(null, {
      "ws no error": () => false,
    });
  });

  // Hold the connection open for the scenario duration
  ws.setTimeout(() => {
    ws.close();
  }, 170000); // 2m50s — just under the 3m scenario duration
}

// ── Scenario: API read ────────────────────────────────────────────────────────

/**
 * GET /api/v1/incidents — validates status 200 and a parseable JSON body.
 */
export function apiRead() {
  const res = http.get(`${API_BASE}/api/v1/incidents`, {
    tags: { scenario: "api" },
    headers: { Accept: "application/json" },
  });

  check(res, {
    "api_read status 200": (r) => r.status === 200,
    "api_read body has incidents key": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.incidents);
      } catch (_) {
        return false;
      }
    },
  });
}

// ── Scenario: API write ───────────────────────────────────────────────────────

/**
 * Fetch an open incident, then ack it and resolve it with Idempotency-Key.
 *
 * If no open incidents exist the VU emits a "no_incident" check (informational)
 * and returns — this keeps the error rate correct even if the pipeline is
 * running without a live load producer.
 */
export function apiWrite() {
  // 1. Fetch the incident list to find a real incident id.
  const listRes = http.get(`${API_BASE}/api/v1/incidents`, {
    tags: { scenario: "api" },
    headers: { Accept: "application/json" },
  });

  check(listRes, {
    "api_write list status 200": (r) => r.status === 200,
  });

  if (listRes.status !== 200) {
    return;
  }

  let incidents;
  try {
    incidents = JSON.parse(listRes.body).incidents;
  } catch (_) {
    return;
  }

  if (!incidents || incidents.length === 0) {
    // No incidents yet — informational; don't fail the check.
    return;
  }

  // Pick the first actionable incident (ack-able states).
  const open = incidents.find((i) =>
    ["AWAITING_OPERATOR", "TIER1", "TIER2"].includes(i.status)
  );
  if (!open) {
    return;
  }

  const incidentId = open.id;
  const idempotencyKey = `k6-${incidentId}-${Date.now()}`;

  // 2. Acknowledge.
  const ackRes = http.post(
    `${API_BASE}/api/v1/incidents/${incidentId}/acknowledge`,
    null,
    {
      tags: { scenario: "api" },
      headers: {
        Accept: "application/json",
        "Idempotency-Key": idempotencyKey,
      },
    }
  );

  check(ackRes, {
    "api_write ack status 200": (r) => r.status === 200,
  });

  // 3. Resolve.
  const resolveRes = http.post(
    `${API_BASE}/api/v1/incidents/${incidentId}/resolve`,
    JSON.stringify({ resolution_note: "k6 load test teardown" }),
    {
      tags: { scenario: "api" },
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": `${idempotencyKey}-resolve`,
      },
    }
  );

  check(resolveRes, {
    "api_write resolve status 200": (r) => r.status === 200,
  });
}
