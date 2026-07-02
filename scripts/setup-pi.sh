#!/usr/bin/env bash
# One-shot Raspberry Pi setup: system packages + systemd service.
# Run from the repo root: sudo scripts/setup-pi.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo scripts/setup-pi.sh" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

apt-get update
apt-get install -y bluez python3-dbus python3-gi python3-pytest

# Ensure the vendored meshlink-core submodule is present.
if [[ ! -f "$REPO_DIR/vendor/meshlink-core/pipeline/pipeline.py" ]]; then
    git -C "$REPO_DIR" submodule update --init
fi

# Install the systemd unit, pointing at wherever the repo actually lives.
sed "s|WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|" \
    "$REPO_DIR/scripts/meshlink-node.service" \
    > /etc/systemd/system/meshlink-node.service
systemctl daemon-reload
systemctl enable meshlink-node

echo "Done. Start with: sudo systemctl start meshlink-node"
echo "Logs:            journalctl -u meshlink-node -f"
