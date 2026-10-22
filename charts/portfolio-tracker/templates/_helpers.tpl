{{/*
Expand the name of the chart.
*/}}
{{- define "portfolio-tracker.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "portfolio-tracker.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "portfolio-tracker.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "portfolio-tracker.labels" -}}
helm.sh/chart: {{ include "portfolio-tracker.chart" . }}
{{ include "portfolio-tracker.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "portfolio-tracker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "portfolio-tracker.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Stable labels for immutable StatefulSet claim templates; overrides are pinned at install time. */}}
{{- define "portfolio-tracker.claimTemplateLabels" -}}
{{- $labels := include "portfolio-tracker.selectorLabels" .root | fromYaml -}}
{{- $_ := set $labels "app.kubernetes.io/managed-by" .root.Release.Service -}}
{{- if .component -}}
{{- $_ := set $labels "app.kubernetes.io/component" .component -}}
{{- end -}}
{{- toYaml (mergeOverwrite $labels (default dict .labels)) -}}
{{- end }}
