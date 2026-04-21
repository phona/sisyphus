{{/*
通用 helpers
*/}}

{{- define "orchestrator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "orchestrator.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "orchestrator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "orchestrator.labels" -}}
helm.sh/chart: {{ include "orchestrator.chart" . }}
{{ include "orchestrator.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "orchestrator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "orchestrator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "orchestrator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "orchestrator.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "orchestrator.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- include "orchestrator.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
PG DSN 的算法：
1) values.postgres.dsn 显式给了 → 直接用（写死的，不带密码）
2) 否则 host/port/database/user 拼，密码运行时从 Secret 读（容器里 envsubst $PGPASSWORD）
*/}}
{{- define "orchestrator.pgDsnEnv" -}}
{{- if .Values.postgres.dsn -}}
- name: SISYPHUS_PG_DSN
  value: {{ .Values.postgres.dsn | quote }}
{{- else -}}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.passwordSecret.name | quote }}
      key: {{ .Values.postgres.passwordSecret.key | quote }}
- name: SISYPHUS_PG_DSN
  value: "postgresql://{{ .Values.postgres.user }}:$(PGPASSWORD)@{{ .Values.postgres.host }}:{{ .Values.postgres.port }}/{{ .Values.postgres.database }}"
{{- end -}}
{{- end -}}

{{/*
观测 DSN：拼到 postgres 同实例不同 db；只在 observability.enabled 开启时输出。
*/}}
{{- define "orchestrator.obsPgDsnEnv" -}}
{{- if .Values.observability.enabled -}}
- name: SISYPHUS_OBS_PG_DSN
  value: "postgresql://{{ .Values.postgres.user }}:$(PGPASSWORD)@{{ .Values.postgres.host }}:{{ .Values.postgres.port }}/{{ .Values.observability.database }}"
- name: SISYPHUS_SNAPSHOT_INTERVAL_SEC
  value: {{ .Values.observability.snapshotIntervalSec | quote }}
{{- end -}}
{{- end -}}
