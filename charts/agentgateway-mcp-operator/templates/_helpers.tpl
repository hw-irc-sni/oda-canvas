{{/* Image: [global.image.registry/]image:version[-prereleaseSuffix] */}}
{{- define "agentgateway-mcp-operator.image" -}}
{{- $registry := "" -}}
{{- with .Values.global }}{{ with .image }}{{ with .registry }}{{ $registry = printf "%s/" (trimSuffix "/" .) }}{{ end }}{{ end }}{{ end -}}
{{- $registry }}{{ .Values.image }}:{{ .Values.version }}{{ with .Values.prereleaseSuffix }}-{{ . }}{{ end }}
{{- end -}}

{{- define "agentgateway-mcp-operator.imagePullPolicy" -}}
{{- if .Values.prereleaseSuffix }}{{ .Values.prereleasePullPolicy | default "Always" }}{{ else }}{{ .Values.imagePullPolicy }}{{ end -}}
{{- end -}}

{{- define "agentgateway-mcp-operator.name" -}}
{{ .Release.Name }}-agentgateway-mcp-op
{{- end -}}

{{- define "agentgateway-mcp-operator.pullSecrets" -}}
{{- with .Values.global }}{{ with .imagePullSecrets }}
imagePullSecrets:
{{- range . }}
{{- if kindIs "map" . }}
- {{ toYaml . | trim }}
{{- else }}
- name: {{ . }}
{{- end }}
{{- end }}
{{- end }}{{ end }}
{{- end -}}
