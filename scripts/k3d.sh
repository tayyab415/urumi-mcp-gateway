#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$LAB_ROOT/.runtime/kubernetes"
chmod 700 "$LAB_ROOT/.runtime/kubernetes"
export DOCKER_HOST="unix://$LAB_ROOT/.runtime/run/docker.sock"
export KUBECONFIG="$LAB_ROOT/.runtime/kubernetes/kubeconfig.yaml"
exec "$LAB_ROOT/.runtime/bin/k3d" "$@"
