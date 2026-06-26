# Deploy — Helm chart, KEDA, and the k8s validation gate

This directory contains the Helm chart for the Factory Monitor stack and the
observability values for kube-prometheus-stack.  The chart targets k3s/k3d for
local demos and any Kubernetes 1.30+ cluster for production use.

---

## What's in here

```
deploy/
├── helm/factory-monitor/       # The application Helm chart (appVersion 0.1.0)
│   ├── Chart.yaml
│   ├── values.yaml             # Defaults (all autoscaling / observability off)
│   ├── values-cloud.yaml       # Live-demo overrides (keda + observability on)
│   ├── templates/
│   │   ├── keda/               # ScaledObjects (ingest-worker, escalation-worker)
│   │   ├── observability/      # PodMonitor, PrometheusRule, Grafana ConfigMaps
│   │   ├── loadgen-job.yaml    # Load generator Job (disabled by default)
│   │   └── …                   # Deployments, Services, StatefulSets, Jobs
│   └── dashboards/             # Six Grafana dashboard JSON files
└── k8s/observability/
    └── kube-prometheus-stack-values.yaml   # kube-prometheus-stack install values
```

---

## Chart feature gates

| Value key | Default | Purpose |
|-----------|---------|---------|
| `keda.enabled` | `false` | Render the two KEDA ScaledObjects |
| `observability.podMonitor.enabled` | `false` | PodMonitor for all workers + API |
| `observability.prometheusRule.enabled` | `false` | Six alerting rules |
| `observability.grafanaDashboards.enabled` | `false` | Six Grafana dashboard ConfigMaps |
| `loadgen.enabled` | `false` | Kafka anomaly load-generator Job |

`values-cloud.yaml` sets all five to `true` and is used for the live demo.

---

## KEDA ScaledObjects

Two ScaledObjects are rendered when `keda.enabled=true`:

**ingest-worker** (`templates/keda/scaledobject-ingest.yaml`)
- Trigger: Kafka consumer-group lag on topic `vision.anomalies.v1`, group
  `ingest-worker`, lag threshold 100 messages.
- Scale range: 1 → 6 replicas.
- Scale-up: +2 pods per 30 s window; scale-down: −1 pod per 60 s after 120 s
  stabilisation.

**escalation-worker** (`templates/keda/scaledobject-escalation.yaml`)
- Trigger: Prometheus query `max(escalation_due_rows)`, threshold 50.
- Scale range: 1 → 4 replicas; same HPA behavior as ingest.

Both ScaledObjects reference Prometheus at
`http://kube-prometheus-stack-prometheus.observability.svc:9090` (overridable via
`keda.prometheusAddress`).

---

## Image import (k3d/k3s — no registry required)

Build all service images, tag them at `0.1.0`, then import into the k3d node so
`imagePullPolicy: IfNotPresent` resolves locally:

```bash
for img in \
  factory-monitor/api:0.1.0 \
  factory-monitor/ingest-worker:0.1.0 \
  factory-monitor/escalation-worker:0.1.0 \
  factory-monitor/notifier-worker:0.1.0 \
  factory-monitor/heatmap-worker:0.1.0 \
  factory-monitor-frontend:0.1.0 \
  factory-monitor/loadgen:0.1.0; do
  k3d image import "$img" -c factory-monitor
done
```

---

## Quick install (live demo)

```bash
helm install factory-monitor ./deploy/helm/factory-monitor \
  -f deploy/helm/factory-monitor/values-cloud.yaml \
  --set keda.enabled=true \
  --namespace factory-monitor \
  --create-namespace \
  --wait
```

See [load/RUNBOOK.md](../load/RUNBOOK.md) for the full step-by-step live demo
including KEDA install, kube-prometheus-stack, the k6 load test run, and the
evidence capture procedure.

---

## Static validation — `make k8s-validate`

The `k8s-validate` target runs helm lint + kubeconform + k6 inspect without a
cluster or network (except for the kubeconform CRD schema fetch on first run):

```bash
make k8s-validate
```

This runs the same steps as the CI `k8s-validate` job:

1. `helm lint deploy/helm/factory-monitor` — chart structure and template errors.
2. `helm template … | kubeconform -strict` — validates all 35 rendered manifests
   against the Kubernetes 1.30.0 schema + the KEDA CRD schemas from the
   datreeio/CRDs-catalog.
3. `k6 inspect load/k6/slo_loadtest.js` — parses the k6 script and reports its
   options (no cluster required).
4. `pytest cloud/tests/test_load_producer.py` — validates that `build_event()`
   produces payloads that satisfy the `AnomalyEvent` schema (no Docker/Kafka
   needed).

> **Note for Windows users**: the `kubeconform` binary and `k6` binary must be on
> `PATH`. The target is tested on Linux/CI; on Windows, run the commands directly or
> use WSL2.

---

## What's validated vs. demo-only

### Validated statically (CI + `make k8s-validate`)

| Check | Tool | Scope |
|-------|------|-------|
| Chart lints without errors | `helm lint` | All templates, all feature-gate combos |
| All 35 manifests are valid Kubernetes 1.30 objects | `kubeconform -strict` | Rendered with `keda.enabled=true`, `observability.*=true` |
| KEDA ScaledObjects conform to the KEDA v1alpha1 CRD schema | `kubeconform` + datreeio CRD catalog | Both ScaledObjects |
| k6 script parses without syntax errors | `k6 inspect` | `load/k6/slo_loadtest.js` |
| `build_event()` round-trips through `AnomalyEvent.model_validate` | `pytest test_load_producer.py` | 7 tests, no network |

### Demo-only (requires the user's live cluster)

| Claim | Why it's demo-only |
|-------|--------------------|
| KEDA scales ingest-worker 1 → 6 replicas under lag | Requires running Kafka, KEDA operator, real lag |
| ingest p95 latency < 2 s at 1000 events/s | Requires live Kafka + ingest worker |
| escalation p95 fire lag < 1 s | Requires live escalation worker + Prometheus |
| Kafka lag sawtooth flattens on replica climb | Grafana screenshot — requires live run |
| k6 thresholds: API p99 < 500 ms, error rate < 1% | Requires live API under load |
| `assert_slo_prom.sh` returns exit 0 | Requires live Prometheus with scraped metrics |

The CI gate proves the manifests and scripts are valid. The SLO numbers, the HPA
scale-out, and the Grafana screenshot are produced by running the live demo in
[load/RUNBOOK.md](../load/RUNBOOK.md).
