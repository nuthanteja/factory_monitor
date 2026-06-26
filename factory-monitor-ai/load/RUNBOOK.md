# Live Demo Runbook — Factory Monitor k3s/KEDA/k6

This runbook documents the end-to-end live demo: standing up a local k3s cluster,
deploying the stack with KEDA autoscaling enabled, driving it with the k6 load test,
and capturing the evidence (Kafka-lag sawtooth, HPA scale-out 1→6, SLO assertions).

> **Scope**: The build CI validates static artifacts — helm lint, kubeconform (35/35
> manifests), k6 inspect, and the Python schema tests. Everything in this runbook
> requires a live cluster and is the user's demo. The CI gate is documented in
> [deploy/README.md](../deploy/README.md).

---

## Prerequisites

| Tool | Version tested |
|------|---------------|
| k3d (or k3s) | k3d v5 / k3s v1.30 |
| kubectl | 1.30+ |
| helm | 3.14+ |
| k6 | 0.51+ |
| docker | 24+ |
| curl, jq, python3 | any recent |

---

## Step 1 — Create the k3d cluster

```bash
k3d cluster create factory-monitor \
  --agents 2 \
  --k3s-arg "--disable=traefik@server:0" \
  --port "8080:80@loadbalancer"
```

Verify:

```bash
kubectl cluster-info
kubectl get nodes
```

---

## Step 2 — Install KEDA

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace \
  --wait
```

Confirm the KEDA operator is running:

```bash
kubectl -n keda get pods
```

---

## Step 3 — Build and import the app images

Build all six service images from the repo root:

```bash
cd factory-monitor-ai

docker build -t factory-monitor/api:0.1.0          -f cloud/api/Dockerfile              .
docker build -t factory-monitor/ingest-worker:0.1.0 -f cloud/workers/ingest/Dockerfile   .
docker build -t factory-monitor/escalation-worker:0.1.0 -f cloud/workers/escalation/Dockerfile .
docker build -t factory-monitor/notifier-worker:0.1.0   -f cloud/workers/notifier/Dockerfile   .
docker build -t factory-monitor/heatmap-worker:0.1.0    -f cloud/workers/heatmap/Dockerfile    .
docker build -t factory-monitor-frontend:0.1.0      -f frontend/Dockerfile              .
```

Import them into the k3d node (avoids a registry):

```bash
for img in \
  factory-monitor/api:0.1.0 \
  factory-monitor/ingest-worker:0.1.0 \
  factory-monitor/escalation-worker:0.1.0 \
  factory-monitor/notifier-worker:0.1.0 \
  factory-monitor/heatmap-worker:0.1.0 \
  factory-monitor-frontend:0.1.0; do
  k3d image import "$img" -c factory-monitor
done
```

Build and import the load-generator image:

```bash
docker build -t factory-monitor/loadgen:0.1.0 -f load/producer/Dockerfile load/producer/
k3d image import factory-monitor/loadgen:0.1.0 -c factory-monitor
```

---

## Step 4 — Install kube-prometheus-stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  --namespace observability \
  --create-namespace \
  -f deploy/k8s/observability/kube-prometheus-stack-values.yaml \
  --wait
```

This configures:
- Grafana sidecar to pick up `grafana_dashboard=1` ConfigMaps from all namespaces.
- Prometheus to scrape PodMonitors and PrometheusRules from all namespaces.
- Tempo and Loki datasources pre-wired in Grafana.

Get the Grafana password:

```bash
kubectl -n observability get secret kube-prometheus-stack-grafana \
  -o jsonpath="{.data.admin-password}" | base64 --decode
```

Port-forward Grafana and Prometheus (keep these open in separate terminals):

```bash
kubectl -n observability port-forward svc/kube-prometheus-stack-grafana 3000:80 &
kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
```

---

## Step 5 — Install the factory-monitor chart with KEDA enabled

```bash
helm install factory-monitor ./deploy/helm/factory-monitor \
  -f deploy/helm/factory-monitor/values-cloud.yaml \
  --set keda.enabled=true \
  --set observability.podMonitor.enabled=true \
  --set observability.prometheusRule.enabled=true \
  --set observability.grafanaDashboards.enabled=true \
  --namespace factory-monitor \
  --create-namespace \
  --wait
```

Set the escalation grace window short so escalation fires within the load window:

```bash
kubectl -n factory-monitor set env deployment/escalation-worker \
  OPERATOR_GRACE_SECONDS=10
```

Confirm all pods are running:

```bash
kubectl -n factory-monitor get pods
kubectl -n factory-monitor get scaledobjects
```

Port-forward the API:

```bash
kubectl -n factory-monitor port-forward svc/factory-monitor-api 8000:8000 &
```

---

## Step 6 — Run the load

### 6a — Helm-deployed load generator (Kafka producer)

Enable the loadgen Job via helm upgrade so it starts firing events into Kafka:

```bash
helm upgrade factory-monitor ./deploy/helm/factory-monitor \
  -f deploy/helm/factory-monitor/values-cloud.yaml \
  --set keda.enabled=true \
  --set observability.podMonitor.enabled=true \
  --set observability.prometheusRule.enabled=true \
  --set observability.grafanaDashboards.enabled=true \
  --set loadgen.enabled=true \
  --set loadgen.image.tag=0.1.0 \
  --namespace factory-monitor \
  --wait
```

Watch the loadgen Job:

```bash
kubectl -n factory-monitor get jobs -w
kubectl -n factory-monitor logs -l app.kubernetes.io/component=loadgen -f
```

### 6b — k6 HTTP/WebSocket SLO load test

In a separate terminal:

```bash
export API_BASE=http://localhost:8000
export WS_URL=ws://localhost:8000/ws/live

k6 run \
  -e API_BASE="${API_BASE}" \
  -e WS_URL="${WS_URL}" \
  --summary-export=load/k6/summary-export.json \
  load/k6/slo_loadtest.js
```

The test runs three concurrent scenarios for 3 minutes:
- `ws_live` — 50 VUs subscribed to `/ws/live`, validating envelope shape + freshness.
- `api_read` — 200 req/s GET `/api/v1/incidents`.
- `api_write` — 50 req/s ack + resolve cycle.

Thresholds: API p99 < 500 ms, error rate < 1%, check pass rate > 99%.

---

## Step 7 — Watch the autoscaling in action

In another terminal, watch the HPA and Deployment replica counts live:

```bash
kubectl -n factory-monitor get hpa,deploy -w
```

Expected sequence:
1. `ingest-worker` starts at 2 replicas (values-cloud.yaml).
2. As Kafka lag on `vision.anomalies.v1` (consumer group `ingest-worker`) exceeds the
   100-message threshold, KEDA fires the HPA and scales up — up to 6 replicas.
3. Once the loadgen ramps down, the lag drains and KEDA scales back to 1 after the
   120 s cooldown.

In Grafana (http://localhost:3000):
- **Dashboard 02 — Event Pipeline**: watch the Kafka lag sawtooth climb and flatten.
- **Dashboard 04 — Worker Fleet / HPA**: the "HPA replicas over time" panel shows the
  `ingest-worker` climb from 2 → 6 and descent back to 1.
- **Dashboard 05 — SLO / Golden Signals**: ingest p95 latency and escalation fire lag
  stay inside the SLO envelopes.

---

## Step 8 — Post-ramp SLO assertion

Wait 60 seconds after the k6 test finishes so Prometheus metrics settle, then run:

```bash
export PROM=http://localhost:9090
bash load/k6/assert_slo_prom.sh
```

Expected output (all three assertions pass):

```
=== Prometheus SLO assertions (http://localhost:9090) ===
PASS  ingest p95 latency (s)                                     = 0.0312  (< 2)
PASS  escalation p95 fire lag (s)                                = 0.1204  (< 1)
PASS  ingest-worker kafka consumer lag                           = 3.0000  (< 100)
===
RESULT: all SLOs satisfied
```

---

## Step 9 — Capture the evidence

The deliverables for the demo are:

| Artifact | Location | How to get it |
|----------|----------|---------------|
| k6 summary | `load/k6/summary-export.json` | Written by k6 `--summary-export` |
| SLO assertion output | terminal / saved with `tee` | `bash assert_slo_prom.sh \| tee slo-assert.txt` |
| Grafana screenshot | Grafana UI | Dashboard 04 — HPA replicas panel + Dashboard 02 — lag sawtooth |

Save the Grafana screenshot showing:
- Lag sawtooth: lag rises during ramp, flattens as replicas climb.
- HPA replicas panel: `ingest-worker` line rising from 2 to 6 then returning to 1.

These three artefacts — `summary-export.json`, `slo-assert.txt`, and the screenshot —
are the "load test evidence" that completes the Phase 4c demo.

---

## Teardown

```bash
helm uninstall factory-monitor -n factory-monitor
helm uninstall kube-prometheus-stack -n observability
helm uninstall keda -n keda
k3d cluster delete factory-monitor
```
