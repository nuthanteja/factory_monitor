{{/*
Expand the name of the chart.
*/}}
{{- define "factory-monitor.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this.
*/}}
{{- define "factory-monitor.fullname" -}}
{{- if .Release.Name }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "factory-monitor.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "factory-monitor.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "factory-monitor.selectorLabels" -}}
app.kubernetes.io/name: {{ include "factory-monitor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
wait-for-db initContainer — blocks until the alembic_version table exists.
Use: {{ include "factory-monitor.waitForDb" . | nindent 8 }}
inside an initContainers list.
*/}}
{{- define "factory-monitor.waitForDb" -}}
- name: wait-for-db
  image: "{{ .Values.postgres.image.repository }}:{{ .Values.postgres.image.tag }}"
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command:
    - sh
    - -c
    - |
      until pg_isready -h postgres -U factory && \
        psql -h postgres -U factory -d factory -tAc \
          "SELECT to_regclass('public.alembic_version')" | grep -q alembic_version; do
        echo "waiting for db migration..."
        sleep 3
      done
  env:
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ include "factory-monitor.fullname" . }}-app
          key: POSTGRES_PASSWORD
{{- end }}
