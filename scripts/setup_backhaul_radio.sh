#!/usr/bin/env bash
# 802.11s mesh-mode configuration for the backhaul radio (Phase 3).
#
# Configures the node's *second* wireless radio (a USB adapter — the Pi's
# onboard radio is reserved for phone-facing duties) into 802.11s mesh mode
# on a dedicated 5 GHz channel, so it never contends with phone traffic.
# This is the physical-layer prerequisite for batman-adv: after this script,
# nearby nodes can see each other at the link layer (`iw dev <iface> station
# dump`) with no mesh routing protocol on top yet — that is added by
# scripts/setup_batman.sh.
#
# Idempotent: safe to re-run on every boot; a radio already joined to the
# mesh is left alone.
#
# Run as root: sudo scripts/setup_backhaul_radio.sh
set -euo pipefail

IFACE="${MESHLINK_BACKHAUL_IFACE:-wlan1}"
MESH_ID="${MESHLINK_MESH_ID:-meshlink-backhaul}"
FREQ_MHZ="${MESHLINK_BACKHAUL_FREQ:-5745}"   # channel 149, 5 GHz

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo scripts/setup_backhaul_radio.sh" >&2
    exit 1
fi

if ! ip link show "$IFACE" &>/dev/null; then
    echo "Backhaul radio '$IFACE' not found. The Pi needs a second radio" >&2
    echo "(USB WiFi adapter with 802.11s support, e.g. mt76- or ath9k-based);" >&2
    echo "the onboard radio stays on phone-facing duties." >&2
    exit 1
fi

if ! iw dev "$IFACE" info &>/dev/null; then
    echo "'$IFACE' exists but is not a cfg80211 wireless device (iw cannot drive it)." >&2
    exit 1
fi

# Already joined to our mesh on the right frequency? Nothing to do.
current="$(iw dev "$IFACE" info)"
if grep -q "type mesh point" <<<"$current" \
        && iw dev "$IFACE" get mesh_param mesh_fwding &>/dev/null \
        && grep -q "channel .*(${FREQ_MHZ} MHz" <<<"$current"; then
    echo "$IFACE already in 802.11s mesh mode on ${FREQ_MHZ} MHz — nothing to do."
    exit 0
fi

# Keep NetworkManager / wpa_supplicant from fighting us over the interface.
if command -v nmcli &>/dev/null; then
    nmcli device set "$IFACE" managed no 2>/dev/null || true
fi

ip link set "$IFACE" down
iw dev "$IFACE" mesh leave 2>/dev/null || true
iw dev "$IFACE" set type mp
ip link set "$IFACE" up
iw dev "$IFACE" mesh join "$MESH_ID" freq "$FREQ_MHZ"

echo "$IFACE joined 802.11s mesh '$MESH_ID' on ${FREQ_MHZ} MHz."
echo "Verify link-layer peers (needs a second configured node in range):"
echo "  iw dev $IFACE station dump"
