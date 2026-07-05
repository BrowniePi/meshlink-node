#!/usr/bin/env bash
# Launch a MeshLink node on macOS for a LAN backhaul test (dev only).
#
# A Mac can't join the batman-adv 802.11s mesh (that's Linux-only), so this
# points the backhaul at ordinary LAN IPs instead of the 10.77.0.x scheme,
# via the MESHLINK_ZONE_TABLE override. Everything above the UDP socket —
# BLE (CoreBluetooth), framing, dedup, the relay pipeline — is the same code
# the Pi mesh runs. See docs/tests/mac-2node-relay-test.md.
#
# Usage (run on each Mac — SAME zone=ip list, only <this-zone-id> differs):
#   ./scripts/run-mac-node.sh <this-zone-id> <zone>=<ip> <zone>=<ip> [...]
#
# Zones need not be contiguous — match whatever destination zone the phone
# app addresses. Example: app sends to zone 3, so Mac A serves zone 1 and
# Mac B serves zone 3:
#   Mac A:  ./scripts/run-mac-node.sh 1 1=192.168.1.10 3=192.168.1.11
#   Mac B:  ./scripts/run-mac-node.sh 3 1=192.168.1.10 3=192.168.1.11
#
# Override IP auto-detection with MESHLINK_LOCAL_IP=<addr> if the wrong
# interface is picked (e.g. VPN/utun up).
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <this-zone-id> <zone>=<ip> <zone>=<ip> [...]" >&2
    echo "  e.g. $0 1 1=192.168.1.10 3=192.168.1.11   # this Mac serves zone 1" >&2
    exit 1
fi

ZONE_ID="$1"; shift
if ! [[ "$ZONE_ID" =~ ^[0-9]+$ ]]; then
    echo "First arg must be this node's numeric zone id." >&2
    exit 1
fi

# Build the "zone=ip,zone=ip" table from the pairs; capture this zone's IP.
table=""
own_entry_ip=""
for pair in "$@"; do
    if [[ ! "$pair" =~ ^([0-9]+)=(.+)$ ]]; then
        echo "Bad zone mapping '$pair' — expected <zone>=<ip>." >&2
        exit 1
    fi
    z="${BASH_REMATCH[1]}"; ip="${BASH_REMATCH[2]}"
    table+="${table:+,}${z}=${ip}"
    [[ "$z" == "$ZONE_ID" ]] && own_entry_ip="$ip"
done

if [[ -z "$own_entry_ip" ]]; then
    echo "This node's zone id ($ZONE_ID) is not in the zone list — add ${ZONE_ID}=<this Mac's IP>." >&2
    exit 1
fi

# This node's own LAN IP (for the /24 broadcast address and a sanity check).
local_ip="${MESHLINK_LOCAL_IP:-}"
if [[ -z "$local_ip" ]]; then
    for iface in en0 en1 en2; do
        local_ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
        [[ -n "$local_ip" ]] && break
    done
fi

# Peers send zone-$ZONE_ID traffic to own_entry_ip — warn if that's not us.
if [[ -n "$local_ip" && "$own_entry_ip" != "$local_ip" ]]; then
    echo "WARNING: zone $ZONE_ID is mapped to $own_entry_ip in the table, but this" >&2
    echo "         Mac's LAN IP looks like $local_ip. Peers send zone-$ZONE_ID" >&2
    echo "         traffic to $own_entry_ip — fix the ${ZONE_ID}=<ip> entry if wrong." >&2
fi

# Subnet broadcast for venue-wide (zone 0xFFFF) messages: x.y.z.255 (/24).
broadcast="${MESHLINK_BACKHAUL_BROADCAST_ADDR:-}"
if [[ -z "$broadcast" && "$local_ip" =~ ^([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+$ ]]; then
    broadcast="${BASH_REMATCH[1]}.255"
fi

cd "$(dirname "$0")/.."

echo "Starting node — zone $ZONE_ID"
echo "  zone table : $table"
echo "  broadcast  : ${broadcast:-<default, unicast-only>}"
echo "  local IP   : ${local_ip:-<unknown>}"
echo
echo "If the two nodes can't reach each other, check the macOS firewall"
echo "(System Settings ▸ Network ▸ Firewall) — allow incoming for python3, or"
echo "turn it off for the test. Grant Bluetooth permission to your terminal on"
echo "first run, or the node advertises nothing."
echo

export MESHLINK_ZONE_ID="$ZONE_ID"
export MESHLINK_ZONE_TABLE="$table"
[[ -n "$broadcast" ]] && export MESHLINK_BACKHAUL_BROADCAST_ADDR="$broadcast"

exec env PYTHONPATH="${PYTHONPATH:-.:vendor/meshlink-core}" python3 -m node.main
