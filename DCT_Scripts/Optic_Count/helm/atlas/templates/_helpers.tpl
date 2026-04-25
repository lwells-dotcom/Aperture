{{/*
Common labels for all resources
*/}}
{{- define "atlas.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/*
Selector labels (subset of common labels used in matchLabels)
*/}}
{{- define "atlas.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Web component labels
*/}}
{{- define "atlas.web.labels" -}}
{{ include "atlas.labels" . }}
app.kubernetes.io/component: web
{{- end }}

{{- define "atlas.web.selectorLabels" -}}
{{ include "atlas.selectorLabels" . }}
app.kubernetes.io/component: web
{{- end }}

{{/*
Postgres component labels
*/}}
{{- define "atlas.postgres.labels" -}}
{{ include "atlas.labels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "atlas.postgres.selectorLabels" -}}
{{ include "atlas.selectorLabels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{/*
Full name with release prefix
*/}}
{{- define "atlas.fullname" -}}
{{ .Release.Name }}-{{ .Chart.Name }}
{{- end }}

{{/*
Secret name
*/}}
{{- define "atlas.secretName" -}}
{{ include "atlas.fullname" . }}-secrets
{{- end }}

{{/*
ConfigMap name
*/}}
{{- define "atlas.configmapName" -}}
{{ include "atlas.fullname" . }}-config
{{- end }}
