#!/usr/bin/env bash
set -u
sa=system:serviceaccount:urumi-app:urumi-provisioner
bash scripts/gcp-vm.sh kube patch namespace store-review-astra --as="$sa" --type=merge --dry-run=server -p '{"metadata":{"labels":{"pod-security.kubernetes.io/enforce":"privileged","pod-security.kubernetes.io/warn":"privileged"}}}' -o jsonpath='{.metadata.labels}'
bash scripts/gcp-vm.sh kube create --as="$sa" --dry-run=server -o name -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: store-review-policy-probe
  labels:
    urumi.io/store: "true"
    pod-security.kubernetes.io/enforce: privileged
YAML
