{{/*
The Component name, <release>-<component.name>. It names the Component, prefixes every ExposedAPI path, and is the
audience the API requires in tokens (COMPONENT_NAME), so everything takes it from here.
*/}}
{{- define "km.componentName" -}}
{{- printf "%s-%s" .Release.Name .Values.component.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Services the ExposedAPIs point at */}}
{{- define "km.apiService" -}}{{ printf "%s-kmapi" .Release.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- define "km.mcpService" -}}{{ printf "%s-kmmcp" .Release.Name | trunc 63 | trimSuffix "-" }}{{- end -}}

{{/* Base paths of the exposed APIs (the gateway routes by prefix and does not rewrite) */}}
{{- define "km.knowledgeBase" -}}/{{ include "km.componentName" . }}/tmf-api/knowledgeManagement/v5{{- end -}}
{{- define "km.processBase" -}}/{{ include "km.componentName" . }}/tmf-api/process/v5{{- end -}}

{{/* Specification URLs: the values, or the OAS the API serves at its in-cluster Service */}}
{{- define "km.apiServiceUrl" -}}
http://{{ include "km.apiService" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.ports.api }}
{{- end -}}
{{- define "km.knowledgeApiSpecification" -}}
{{- .Values.component.knowledgeApiSpecification | default (printf "%s%s/openapi.yaml" (include "km.apiServiceUrl" .) (include "km.knowledgeBase" .)) -}}
{{- end -}}
{{- define "km.processFlowApiSpecification" -}}
{{- .Values.component.processFlowApiSpecification | default (printf "%s%s/openapi.yaml" (include "km.apiServiceUrl" .) (include "km.processBase" .)) -}}
{{- end -}}

{{/* Labels of every object; the Component name label links them to the Component, the id label is for discovery */}}
{{- define "km.labels" -}}
oda.tmforum.org/componentName: {{ include "km.componentName" . }}
oda.tmforum.org/componentId: {{ .Values.component.id }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* The enabled dependent APIs, in name order; fails when an enabled one has no specification URL */}}
{{- define "km.enabledDependentAPIs" -}}
{{- $out := dict -}}
{{- range $name, $dep := .Values.dependentAPIs -}}
{{- if $dep.enabled -}}
{{- if not $dep.specification -}}
{{- fail (printf "dependentAPIs.%s is enabled but has no specification URL (TMFOP005 matches providers by that URL)" $name) -}}
{{- end -}}
{{- $_ := set $out $name $dep -}}
{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}
