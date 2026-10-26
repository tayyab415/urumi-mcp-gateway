#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_DOCKER_VERSION="29.9.1"
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "This setup script targets this x86_64 Linux VM."
  exit 1
fi
if ! command -v newuidmap >/dev/null || ! command -v slirp4netns >/dev/null || ! command -v fuse-overlayfs >/dev/null; then
  sudo apt-get install -y --no-install-recommends uidmap slirp4netns fuse-overlayfs
fi
mkdir -p "$LAB_ROOT/.runtime/bin" "$LAB_ROOT/.runtime/downloads" "$LAB_ROOT/.runtime/run"
chmod 700 "$LAB_ROOT/.runtime/run"
if [[ ! -x "$LAB_ROOT/.runtime/bin/docker" ]]; then
  for archive in docker docker-rootless-extras; do
    curl -fL --retry 2 \
      "https://download.docker.com/linux/static/stable/x86_64/$archive-$LAB_DOCKER_VERSION.tgz" \
      -o "$LAB_ROOT/.runtime/downloads/$archive-$LAB_DOCKER_VERSION.tgz"
    tar -xzf "$LAB_ROOT/.runtime/downloads/$archive-$LAB_DOCKER_VERSION.tgz" \
      --strip-components=1 -C "$LAB_ROOT/.runtime/bin"
  done
fi
if [[ ! -x /usr/bin/rootlesskit ]]; then
  sudo install -m 0755 "$LAB_ROOT/.runtime/bin/rootlesskit" /usr/bin/rootlesskit
fi
ln -sf /usr/bin/rootlesskit "$LAB_ROOT/.runtime/bin/rootlesskit"
if ! [[ -r /etc/apparmor.d/rootlesskit ]]; then
  echo "This host lacks Ubuntu's RootlessKit AppArmor profile. Consult the rootless Docker setup docs before starting."
  exit 1
fi
"$LAB_ROOT/.runtime/bin/docker" --version
echo "Runtime ready. Start scripts/start-engine.sh, then build labs/shop."
