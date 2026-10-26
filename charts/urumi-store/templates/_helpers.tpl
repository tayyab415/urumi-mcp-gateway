{{/* A Helm release name is at most 53 characters; component suffixes fit 63. */}}
{{- define "urumi-store.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
{{- end }}

{{/* Pod labels leave out the chart version, so bumping it doesn't restart Pods. */}}
{{- define "urumi-store.podLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "urumi-store.image" -}}
{{ printf "%s:%s@%s" .repository .tag .digest | quote }}
{{- end }}

{{- define "urumi-store.validate" -}}
{{- if eq .Values.database.user "root" -}}
{{- fail "database.user must be a dedicated WordPress user, not root" -}}
{{- end -}}
{{- if .Values.ingress.enabled -}}
{{- $url := urlParse .Values.wordpress.publicUrl -}}
{{- $host := regexReplaceAll ":[0-9]+$" $url.host "" -}}
{{- if ne $host .Values.ingress.host -}}
{{- fail "wordpress.publicUrl hostname must match ingress.host" -}}
{{- end -}}
{{- if and .Values.ingress.tls.enabled (ne $url.scheme "https") -}}
{{- fail "TLS Ingress requires an https wordpress.publicUrl" -}}
{{- end -}}
{{- if and .Values.ingress.tls.enabled (empty .Values.ingress.tls.secretName) -}}
{{- fail "ingress.tls.secretName is required when TLS is enabled" -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
WordPress reads its database settings from these variables at runtime, because
wp-config.php calls getenv(). The store pod and the setup Job must share them.
*/}}
{{- define "urumi-store.wordpressEnv" -}}
- name: WORDPRESS_DB_HOST
  value: {{ .Release.Name }}-mariadb
- name: WORDPRESS_DB_NAME
  value: {{ .Values.database.name | quote }}
- name: WORDPRESS_DB_USER
  value: {{ .Values.database.user | quote }}
- name: WORDPRESS_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.credentials.existingSecret }}
      key: wordpress-db-password
- name: WORDPRESS_PUBLIC_URL
  value: {{ .Values.wordpress.publicUrl | quote }}
- name: WORDPRESS_CONFIG_EXTRA
  value: |
    define('WP_HOME', getenv('WORDPRESS_PUBLIC_URL'));
    define('WP_SITEURL', getenv('WORDPRESS_PUBLIC_URL'));
    define('WP_MEMORY_LIMIT', '256M');
    define('WP_MAX_MEMORY_LIMIT', '256M');
{{- end }}
