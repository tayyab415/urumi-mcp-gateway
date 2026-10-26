#!/usr/bin/env bash
# Runs on the new application VM. It never changes the coding workspace VM.
set -euo pipefail
umask 022
export DEBIAN_FRONTEND=noninteractive

if [[ -f /srv/urumi/bootstrap-complete ]]; then
  exit 0
fi

apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y \
  ca-certificates curl docker.io python3
systemctl enable --now docker
install -d -m 0755 /srv/urumi/tools/scripts /srv/urumi/storage
touch /srv/urumi/bootstrap-complete
echo "Urumi application VM bootstrap complete."
