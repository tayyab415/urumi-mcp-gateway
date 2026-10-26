#!/usr/bin/env bash
# Run as root on the application VM after copying the tool installer.
set -euo pipefail
umask 077
cd /srv/urumi/tools

if [[ ! -x .runtime/bin/k3d ]]; then
  python3 scripts/setup-kubernetes-tools.py
fi

export PATH="/srv/urumi/tools/.runtime/bin:$PATH"
cluster_name="urumi-vm"
if ! k3d cluster list -o json | python3 -c \
  'import json,sys; sys.exit(not any(c["name"] == "urumi-vm" for c in json.load(sys.stdin)))'
then
  k3d cluster create "$cluster_name" \
    --image rancher/k3s:v1.36.5-k3s1 \
    --servers 1 \
    --agents 0 \
    --servers-memory 6g \
    --api-port 127.0.0.1:16443 \
    --port 127.0.0.1:18080:80@loadbalancer \
    --port 80:80@loadbalancer \
    --port 443:443@loadbalancer \
    --volume /srv/urumi/storage:/var/lib/rancher/k3s/storage@server:0 \
    --kubeconfig-update-default=false \
    --kubeconfig-switch-context=false \
    --timeout 180s
fi

install -d -m 0700 /srv/urumi/tools/.runtime/kubernetes
k3d kubeconfig get "$cluster_name" \
  > /srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml
chmod 0600 /srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml

# This service starts the existing cluster after the VM boots.
# It does not create a new cluster or replace stored data.
cat > /etc/systemd/system/urumi-cluster.service <<'UNIT'
[Unit]
Description=Start the Urumi Kubernetes cluster
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/srv/urumi/tools/.runtime/bin/k3d cluster start urumi-vm --timeout 180s
RemainAfterExit=yes
TimeoutStartSec=240
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable urumi-cluster.service
systemctl start urumi-cluster.service

export KUBECONFIG=/srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml
kubectl wait --for=condition=Ready node/k3d-urumi-vm-server-0 --timeout=120s
kubectl get nodes
