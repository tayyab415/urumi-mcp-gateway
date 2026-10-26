#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DOCKER_HOST="unix://$LAB_ROOT/.runtime/run/docker.sock"
export DOCKER_BUILDKIT=0
exec "$LAB_ROOT/.runtime/bin/docker" "$@"
