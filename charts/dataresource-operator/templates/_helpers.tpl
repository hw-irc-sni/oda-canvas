{{/* Image: [global.image.registry/]image:version[-prereleaseSuffix] */}}
{{- define "dataresource-operator.image" -}}
{{- $registry := "" -}}
{{- with .Values.global }}{{ with .image }}{{ with .registry }}{{ $registry = printf "%s/" (trimSuffix "/" .) }}{{ end }}{{ end }}{{ end -}}
{{- $registry }}{{ .Values.image }}:{{ .Values.version }}{{ with .Values.prereleaseSuffix }}-{{ . }}{{ end }}
{{- end -}}

{{/* A prerelease image is pulled with prereleasePullPolicy (default Always, as the other Canvas operators) */}}
{{- define "dataresource-operator.imagePullPolicy" -}}
{{- if .Values.prereleaseSuffix }}{{ .Values.prereleasePullPolicy | default "Always" }}{{ else }}{{ .Values.imagePullPolicy }}{{ end -}}
{{- end -}}

{{- define "dataresource-operator.name" -}}
{{ .Release.Name }}-dataresource-op
{{- end -}}

{{- define "dataresource-operator.pullSecrets" -}}
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

{{- define "dataresource-operator.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Environment of the setup and teardown Jobs */}}
{{- define "dataresource-operator.setupEnv" -}}
- name: KEYCLOAK_URL
  value: {{ .Values.keycloak.url | quote }}
- name: KEYCLOAK_REALM
  value: {{ .Values.keycloak.realm | quote }}
- name: KC_ADMIN_USER
  value: {{ .Values.keycloak.adminUser | quote }}
- name: KC_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.keycloak.adminSecret }}
      key: {{ .Values.keycloak.adminPasswordKey }}
- name: OPERATOR_CLIENT_ID
  value: {{ .Values.oidc.clientId | quote }}
- name: OIDC_SECRET_NAME
  value: {{ .Values.oidc.secretName | quote }}
- name: POD_NAMESPACE
  valueFrom:
    fieldRef:
      fieldPath: metadata.namespace
- name: SETUP_TIMEOUT_SECONDS
  value: {{ .Values.keycloak.setup.timeoutSeconds | quote }}
{{- end -}}
