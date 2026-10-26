#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$LAB_UNIT_DIR"
cat > "$LAB_UNIT_DIR/urumi-learning-engine.service" <<EOF
[Unit]
Description=Urumi learning rootless Docker engine

[Service]
Type=simple
ExecStart=$LAB_ROOT/scripts/start-engine.sh
WorkingDirectory=$LAB_ROOT
Delegate=yes
Restart=on-failure
RestartSec=2
TimeoutStopSec=30
StandardOutput=append:$LAB_ROOT/.runtime/engine.log
StandardError=append:$LAB_ROOT/.runtime/engine.log

[Install]
WantedBy=default.target
EOF
cat > "$LAB_UNIT_DIR/urumi-container-console.service" <<EOF
[Unit]
Description=Urumi stage one teaching console
After=urumi-learning-engine.service

[Service]
Type=simple
ExecStart=$LAB_ROOT/scripts/start-console.sh
WorkingDirectory=$LAB_ROOT
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
echo "User services installed. They are not enabled at login."
echo "Start urumi-learning-engine, then urumi-container-console."
