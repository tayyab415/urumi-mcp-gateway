#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAB_ROOT"

python3 - <<'PY'
import shutil
from pathlib import Path

free = shutil.disk_usage(Path.cwd()).free / 1024**3
if free < 4:
    raise SystemExit(
        f"Only {free:.2f} GiB free. Need at least 4 GiB to start the empty lab cluster."
    )
print(f"Free disk: {free:.2f} GiB. This check covers the empty cluster, not the store.")
PY

if ! bash scripts/docker-lab.sh info >/dev/null 2>&1; then
  systemctl --user start urumi-learning-engine.service
fi

bash scripts/k3d.sh cluster create urumi-local \
  --image rancher/k3s:v1.36.5-k3s1 \
  --servers 1 \
  --agents 0 \
  --servers-memory 4g \
  --api-port 127.0.0.1:16443 \
  --port 127.0.0.1:18080:80@loadbalancer \
  --kubeconfig-update-default=false \
  --kubeconfig-switch-context=false \
  --k3s-arg '--kubelet-arg=feature-gates=KubeletInUserNamespace=true@server:0' \
  --k3s-arg '--kube-proxy-arg=conntrack-max-per-core=0@server:0' \
  --k3s-arg '--kube-proxy-arg=conntrack-tcp-timeout-established=0s@server:0' \
  --k3s-arg '--kube-proxy-arg=conntrack-tcp-timeout-close-wait=0s@server:0' \
  --timeout 180s

umask 077
bash scripts/k3d.sh kubeconfig get urumi-local \
  > .runtime/kubernetes/kubeconfig.yaml

bash scripts/kubectl.sh wait \
  --for=condition=Ready node/k3d-urumi-local-server-0 --timeout=120s
bash scripts/kubectl.sh get nodes
