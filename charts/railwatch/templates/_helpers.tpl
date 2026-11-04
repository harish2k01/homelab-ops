{{/*
Expand the name of the chart.
*/}}
{{- define "railwatch.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "railwatch.fullname" -}}
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

{{- define "railwatch.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels. They match the workloads created before this chart, so keep them
to name + component only: Deployment and StatefulSet selectors are immutable.
Usage: include "railwatch.selectorLabels" (list . "backend")
*/}}
{{- define "railwatch.selectorLabels" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/component: {{ index . 1 }}
app.kubernetes.io/name: {{ include "railwatch.name" $root }}
{{- end }}

{{/*
Common labels. Usage: include "railwatch.labels" (list . "backend")
*/}}
{{- define "railwatch.labels" -}}
{{- $root := index . 0 -}}
helm.sh/chart: {{ include "railwatch.chart" $root }}
{{ include "railwatch.selectorLabels" . }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
{{- if $root.Chart.AppVersion }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
{{- end }}

{{/* A digest identifies the exact published release image. */}}
{{- define "railwatch.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}
{{- end -}}

{{- define "railwatch.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "railwatch.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "railwatch.secretName" -}}
{{- required "existingSecret must name the externally managed RailWatch Secret" .Values.existingSecret -}}
{{- end -}}

{{/* Application pod probes; both roles expose the same health routes. */}}
{{- define "railwatch.probes" -}}
startupProbe:
  httpGet:
    path: /api/health/live
    port: http
  periodSeconds: 3
  timeoutSeconds: 5
  failureThreshold: 60
livenessProbe:
  httpGet:
    path: /api/health/live
    port: http
  periodSeconds: 20
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /api/health/ready
    port: http
  periodSeconds: 5
  timeoutSeconds: 5
  failureThreshold: 3
{{- end -}}

{{- define "railwatch.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
