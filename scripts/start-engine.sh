#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$LAB_ROOT/.runtime/bin:$PATH"
export XDG_RUNTIME_DIR="$LAB_ROOT/.runtime/run"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
exec "$LAB_ROOT/.runtime/bin/dockerd-rootless.sh" \
  --host="unix://$XDG_RUNTIME_DIR/docker.sock" \
  --data-root="$LAB_ROOT/.runtime/engine" \
  --exec-root="$XDG_RUNTIME_DIR/exec" \
  --pidfile="$XDG_RUNTIME_DIR/docker.pid" \
  --storage-driver=fuse-overlayfs
