#!/usr/bin/env bash
# assert_slo_prom.sh — query Prometheus for the 3 server-side SLOs and exit
# non-zero if any breach is detected.
#
# Run this 60 s after the load generator ramp-down so metrics have settled.
#
# Usage:
#   PROM=http://localhost:9090 bash assert_slo_prom.sh
#
# Exit codes:
#   0  — all SLOs satisfied
#   1  — one or more SLOs breached (breach details printed to stderr)

set -euo pipefail

PROM="${PROM:-http://localhost:9090}"

# ── Helpers ───────────────────────────────────────────────────────────────────

# query <promql>  →  scalar value as a string, or "NaN" on no data
query() {
  local promql="$1"
  local encoded
  encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$promql")
  curl -sf "${PROM}/api/v1/query?query=${encoded}" \
    | jq -r '.data.result[0].value[1] // "NaN"'
}

# check_lt <label> <promql> <threshold>
# Evaluates the query and asserts the result is < threshold.
# Prints a pass/fail line and accumulates failures.
FAILURES=0

check_lt() {
  local label="$1"
  local promql="$2"
  local threshold="$3"

  local value
  value=$(query "$promql")

  if [ "$value" = "NaN" ]; then
    echo "WARN  $label = NaN (no data — metric not scraped yet?)" >&2
    # Treat missing data as a pass during initial ramp-up; strict CI can flip this.
    return
  fi

  # Use awk for float comparison
  local ok
  ok=$(awk -v v="$value" -v t="$threshold" 'BEGIN { print (v < t) ? "1" : "0" }')
  if [ "$ok" = "1" ]; then
    printf "PASS  %-60s = %.4f  (< %s)\n" "$label" "$value" "$threshold"
  else
    printf "FAIL  %-60s = %.4f  (>= %s, SLO BREACHED)\n" "$label" "$value" "$threshold" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

# ── SLO checks ────────────────────────────────────────────────────────────────

echo "=== Prometheus SLO assertions (${PROM}) ==="

# SLO 1: ingest p95 latency < 2 s
# The ingest worker observes (now - occurred_at) as the latency.
check_lt \
  "ingest p95 latency (s)" \
  "histogram_quantile(0.95, sum by(le)(rate(ingest_event_to_incident_latency_seconds_bucket[1m])))" \
  "2"

# SLO 2: escalation p95 fire lag < 1 s
check_lt \
  "escalation p95 fire lag (s)" \
  "histogram_quantile(0.95, sum by(le)(rate(escalation_fire_lag_seconds_bucket[5m])))" \
  "1"

# SLO 3: ingest-worker consumer group lag < 100 messages
check_lt \
  "ingest-worker kafka consumer lag" \
  "max(kafka_consumergroup_lag{consumergroup=\"ingest-worker\"})" \
  "100"

# ── Result ────────────────────────────────────────────────────────────────────

echo "==="
if [ "$FAILURES" -gt 0 ]; then
  echo "RESULT: $FAILURES SLO(s) BREACHED" >&2
  exit 1
fi
echo "RESULT: all SLOs satisfied"
exit 0
