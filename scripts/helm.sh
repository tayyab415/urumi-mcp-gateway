#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="$LAB_ROOT/.runtime/kubernetes/kubeconfig.yaml"
export HELM_CACHE_HOME="$LAB_ROOT/.runtime/helm/cache"
export HELM_CONFIG_HOME="$LAB_ROOT/.runtime/helm/config"
export HELM_DATA_HOME="$LAB_ROOT/.runtime/helm/data"
exec "$LAB_ROOT/.runtime/bin/helm" "$@"
