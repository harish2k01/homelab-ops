{{/*
Expand the name of the chart.
*/}}
{{- define "pve-exporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fullname using release name.
*/}}
{{- define "pve-exporter.fullname" -}}
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
Common labels
*/}}
{{- define "pve-exporter.labels" -}}
app.kubernetes.io/name: {{ include "pve-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/component: monitoring
app.kubernetes.io/part-of: pve-exporter
{{- end }}

{{/*
Selector labels
*/}}
{{- define "pve-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pve-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
