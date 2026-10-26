#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="$LAB_ROOT/.runtime/kubernetes/kubeconfig.yaml"
exec "$LAB_ROOT/.runtime/bin/kubectl" "$@"
