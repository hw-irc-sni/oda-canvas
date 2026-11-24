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

{{/* More object names */}}
{{- define "km.name" -}}{{ printf "%s-%s" .root.Release.Name .suffix | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- define "km.workerName" -}}{{ include "km.name" (dict "root" . "suffix" "kmworker") }}{{- end -}}
{{- define "km.internalService" -}}{{ include "km.name" (dict "root" . "suffix" "kminternal") }}{{- end -}}
{{- define "km.headlessService" -}}{{ include "km.name" (dict "root" . "suffix" "kmapi-hl") }}{{- end -}}
{{- define "km.tokenSecret" -}}{{ include "km.name" (dict "root" . "suffix" "km-internal-token") }}{{- end -}}
{{- define "km.serviceAccount" -}}{{ .Values.serviceAccount.name | default (include "km.name" (dict "root" . "suffix" "km")) }}{{- end -}}

{{/* Selector labels of a workload (stable across upgrades); .workload is api, worker or mcp */}}
{{- define "km.selectorLabels" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .workload }}
{{- end -}}

{{/* Image of a workload: .img is its values block (image, version, prereleaseSuffix, tag) */}}
{{- define "km.image" -}}
{{- $tag := .tag | default (ternary (printf "%s-%s" .version .prereleaseSuffix) .version (not (empty .prereleaseSuffix))) -}}
{{- printf "%s:%s" .image $tag -}}
{{- end -}}

{{/* Security context of every container */}}
{{- define "km.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities:
  drop: [ALL]
{{- end -}}
{{- define "km.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Scheduling and annotations of a workload's pod spec, from its values block */}}
{{- define "km.scheduling" -}}
{{- with .nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* The Keycloak realm URL inside the cluster */}}
{{- define "km.realmUrl" -}}{{ trimSuffix "/" .Values.keycloak.baseUrl }}/realms/{{ .Values.keycloak.realm }}{{- end -}}
