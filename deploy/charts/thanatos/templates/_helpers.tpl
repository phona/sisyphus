{{/*
Expand the name of the chart.
*/}}
{{- define "thanatos.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Truncated at 63 chars (DNS-1123 limit).
*/}}
{{- define "thanatos.fullname" -}}
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

{{/*
Chart label.
*/}}
{{- define "thanatos.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "thanatos.labels" -}}
helm.sh/chart: {{ include "thanatos.chart" . }}
{{ include "thanatos.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
sisyphus.phona/component: acceptance-harness
sisyphus.phona/driver: {{ .Values.driver | quote }}
{{- end -}}

{{/*
Selector labels.
*/}}
{{- define "thanatos.selectorLabels" -}}
app.kubernetes.io/name: {{ include "thanatos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Driver guard. helm template fails fast if driver is not in the allow-list.
*/}}
{{- define "thanatos.assertDriver" -}}
{{- $allowed := list "playwright" "adb" "http" -}}
{{- if not (has .Values.driver $allowed) -}}
{{- fail (printf "thanatos.driver must be one of playwright|adb|http, got %q" .Values.driver) -}}
{{- end -}}
{{- if and (eq .Values.driver "adb") (empty .Values.redroid.image) -}}
{{- fail "thanatos.driver=adb requires redroid.image to be set" -}}
{{- end -}}
{{- end -}}
