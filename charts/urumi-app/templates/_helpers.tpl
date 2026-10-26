{{/* Labels on every object this chart makes. */}}
{{- define "urumi-app.labels" -}}
app.kubernetes.io/name: urumi-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* The labels that pick one component's Pods: (dict "root" $ "component" "gateway"). */}}
{{- define "urumi-app.selector" -}}
app.kubernetes.io/name: urumi-app
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* repository:tag, refusing an empty tag so a forgotten build fails at install time. */}}
{{- define "urumi-app.image" -}}
{{- if not .tag -}}
{{- fail (printf "images: no tag for %s. Run: bash scripts/gcp-vm.sh build-images" .repository) -}}
{{- end -}}
{{ .repository }}:{{ .tag }}
{{- end }}

{{/*
Pod security for every app Pod: a fixed non-root user and the runtime's default
seccomp filter. With the container settings below, this meets the "restricted"
Pod Security Standard, which the namespace enforces. Pass the user id.
*/}}
{{- define "urumi-app.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* No privilege escalation, no Linux capabilities, and a read-only image. */}}
{{- define "urumi-app.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* One env var from the app Secret: (dict "root" $ "name" "ENV_NAME" "key" "secret-key" "optional" false). */}}
{{- define "urumi-app.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.secretName }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end }}

{{/* An egress rule for DNS lookups through the cluster's CoreDNS. */}}
{{- define "urumi-app.dnsEgress" -}}
- to:
    - namespaceSelector:
        matchLabels: {kubernetes.io/metadata.name: kube-system}
      podSelector:
        matchLabels: {k8s-app: kube-dns}
  ports:
    - {port: 53, protocol: UDP}
    - {port: 53, protocol: TCP}
{{- end }}
