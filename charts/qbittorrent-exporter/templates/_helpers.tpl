{{/*
Expand the name of the chart.
*/}}
{{- define "qbittorrent-exporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fullname using release name.
*/}}
{{- define "qbittorrent-exporter.fullname" -}}
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
{{- define "qbittorrent-exporter.labels" -}}
app.kubernetes.io/name: {{ include "qbittorrent-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/component: monitoring
app.kubernetes.io/part-of: qbittorrent-exporter
{{- end }}

{{/*
Selector labels
*/}}
{{- define "qbittorrent-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "qbittorrent-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Secret name for qBittorrent credentials
*/}}
{{- define "qbittorrent-exporter.secretName" -}}
{{- if .Values.qbittorrent.existingSecret }}
{{- .Values.qbittorrent.existingSecret }}
{{- else }}
{{- include "qbittorrent-exporter.fullname" . }}
{{- end }}
{{- end }}
