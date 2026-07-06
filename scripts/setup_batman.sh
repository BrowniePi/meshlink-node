#!/usr/bin/env bash
# batman-adv mesh routing on top of the 802.11s backhaul radio (Phase 3).
#
# Loads the batman-adv kernel module (and makes it load on boot), attaches
# the backhaul radio to the bat0 virtual interface, and gives this node its
# unique IP on the mesh subnet (10.77.0.<node-id>/24). After this script,
# every node can reach every other node over standard Layer-2/3 networking
# on bat0, with self-healing multi-hop routing handled by batman-adv.
#
# Prerequisite: scripts/setup_backhaul_radio.sh (the radio must already be
# in 802.11s mesh mode — batman-adv rides on that link layer).
#
# Idempotent: safe to re-run on every boot (`ip addr replace`, interface
# add is skipped if already attached).
#
# Run as root: sudo MESHLINK_NODE_ID=1 scripts/setup_batman.sh
set -euo pipefail

IFACE="${MESHLINK_BACKHAUL_IFACE:-wlan1}"
BAT_IFACE="bat0"
MESH_SUBNET_PREFIX="10.77.0"   # must match node/backhaul/static_zone_table.py
NODE_ID="${MESHLINK_NODE_ID:-}"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo MESHLINK_NODE_ID=<1|2|3> scripts/setup_batman.sh" >&2
    exit 1
fi

if [[ ! "$NODE_ID" =~ ^[0-9]+$ ]] || (( NODE_ID < 1 || NODE_ID > 254 )); then
    echo "Set MESHLINK_NODE_ID to this node's number (1, 2, or 3 for the" >&2
    echo "Phase 3 test deployment) — it becomes the bat0 IP ${MESH_SUBNET_PREFIX}.<id>." >&2
    exit 1
fi

if ! command -v batctl &>/dev/null; then
    apt-get update && apt-get install -y batctl
fi

modprobe batman-adv
# Load on every boot, not just this one.
echo batman-adv > /etc/modules-load.d/batman-adv.conf

# Attach the backhaul radio to bat0 (skip if already attached).
if ! batctl if 2>/dev/null | grep -q "^${IFACE}:"; then
    batctl if add "$IFACE"
fi

ip link set up dev "$IFACE"
ip link set up dev "$BAT_IFACE"
ip addr replace "${MESH_SUBNET_PREFIX}.${NODE_ID}/24" dev "$BAT_IFACE"

echo "bat0 up as ${MESH_SUBNET_PREFIX}.${NODE_ID}/24 over ${IFACE}."
echo "Verify mesh neighbours (needs another configured node in range):"
echo "  batctl o                          # originator table — other nodes appear here"
echo "  ping ${MESH_SUBNET_PREFIX}.<other-id>   # basic reachability over bat0"
