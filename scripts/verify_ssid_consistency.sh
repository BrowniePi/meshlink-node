#!/usr/bin/env bash
# Boot-time SSID consistency self-check (Phase 6).
#
# A node whose SSID/passphrase drift from the deployment-wide values silently
# becomes an isolated WiFi island — phones roaming into its coverage see a
# different network and never associate. This script verifies the chain
#
#   deployment config (source of truth)
#     -> installed /etc/hostapd/meshlink.conf
#     -> SSID actually on the air (iw)
#
# and exits non-zero with a clear message on any mismatch. Run it at boot
# after setup_hostapd.sh (and any time deployment config is re-pushed).
#
# Run from the repo root: scripts/verify_ssid_consistency.sh
set -euo pipefail

IFACE="${MESHLINK_AP_IFACE:-wlan0}"
HOSTAPD_CONF="/etc/hostapd/meshlink.conf"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() {
    echo "SSID CONSISTENCY FAILURE: $*" >&2
    echo "Re-push the deployment config and re-run scripts/setup_hostapd.sh" >&2
    echo "(see docs/wifi-ap-deployment.md)." >&2
    exit 1
}

# 1. Expected SSID from the deployment config (validated by the Python side;
#    prints the SSID only, never the passphrase).
cd "$REPO_ROOT"
expected_ssid="$(python3 -m node.wifi_ap.deployment_config)" \
    || fail "deployment config missing or invalid"

# 2. Installed hostapd config must match the deployment config — catches a
#    re-pushed config that setup_hostapd.sh was never re-run for, and any
#    hand-edited /etc/hostapd.
[[ -f "$HOSTAPD_CONF" ]] || fail "$HOSTAPD_CONF not installed — run scripts/setup_hostapd.sh"
installed_ssid="$(grep '^ssid=' "$HOSTAPD_CONF" | cut -d= -f2-)"
[[ "$installed_ssid" == "$expected_ssid" ]] \
    || fail "installed hostapd SSID '$installed_ssid' != deployment SSID '$expected_ssid'"

# sed (not xargs) to trim: xargs chokes on quotes, which a passphrase may contain.
expected_pass="$(grep '^[[:space:]]*passphrase[[:space:]]*=' \
    "${MESHLINK_WIFI_DEPLOYMENT_CONF:-/etc/meshlink/wifi_deployment.conf}" \
    | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
installed_pass="$(grep '^wpa_passphrase=' "$HOSTAPD_CONF" | cut -d= -f2-)"
[[ "$installed_pass" == "$expected_pass" ]] \
    || fail "installed WPA passphrase differs from deployment config"

# 3. What the radio is actually broadcasting right now.
onair="$(iw dev "$IFACE" info 2>/dev/null)" || fail "cannot query $IFACE via iw"
grep -q '^\s*type AP' <<<"$onair" || fail "$IFACE is not in AP mode"
onair_ssid="$(sed -n 's/^\s*ssid \(.*\)$/\1/p' <<<"$onair")"
[[ "$onair_ssid" == "$expected_ssid" ]] \
    || fail "$IFACE broadcasting '$onair_ssid' != deployment SSID '$expected_ssid'"

echo "SSID consistency OK: $IFACE broadcasting '$expected_ssid' per deployment config."
