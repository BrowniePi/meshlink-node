# Phase 6 node-side decisions not specified in Notion

Choices made during implementation that the Phase 6 page, task pages, and
WiFi Mesh Add-On doc leave open. Everything else follows those docs directly.

- **AP radio = `wlan0` (Pi onboard).** The Add-On says "the same radio used
  for BLE-adjacent duties, or a separate dedicated radio". BLE serving uses
  the Bluetooth radio and the backhaul owns `wlan1`, so the onboard WiFi
  radio is free. Overridable via `MESHLINK_AP_IFACE`.

- **AP band/channel = 2.4 GHz, channel 6.** The docs only require a channel
  distinct from the backhaul's band/channel. The backhaul is on 5745 MHz
  (channel 149, 5 GHz), so putting the AP on 2.4 GHz gives whole-band
  separation, and 2.4 GHz carries further — the range upgrade is half the
  point of this phase. Per-node override via `MESHLINK_AP_CHANNEL` (1–13
  only; 5 GHz is rejected to protect the backhaul band) for venue RF
  planning — same-SSID nodes may sit on different channels in an ESS.

- **Security = WPA2-PSK (CCMP).** The docs say "SSID and passphrase" without
  naming a WPA mode. WPA2-PSK is what every target phone supports; WPA3-SAE
  transition mode deferred until tested on the Pi's onboard radio.

- **Central config mechanism = a file pushed to every node**
  (`/etc/meshlink/wifi_deployment.conf`, flat `key=value`), not a
  backend fetch. Works air-gapped, keeps the backend off the venue path
  (Phase 5 rule: backend is touched only for the boot key-fetch and
  heartbeats), and "pushed from central deployment config" describes a file
  push naturally. Validation is strict — unknown keys, bad lengths, or a
  missing file abort AP setup rather than let a node broadcast wrong values.

- **DHCP for phones = dnsmasq on 10.78.0.0/24, no gateway/DNS offered.**
  The docs require a closed network but don't say how phones get addresses;
  without DHCP, modern phones fail the join. Empty dhcp-options 3/6 mean no
  phone ever routes general traffic at us. Subnet chosen next to (not
  overlapping) the 10.77.0.0/24 backhaul.

- **Roaming assists: 802.11k + 802.11v enabled now, 802.11r deferred.** The
  Add-On names all three; k and v are one hostapd flag each, while r needs
  cross-node key material (mobility domain, r0kh/r1kh) — that belongs with
  Phase 7's dynamic node coordination.

- **Tasks 1–2 left the node process (`main.py`)/relay untouched** — they are
  system configuration; the boot self-check is
  `scripts/verify_ssid_consistency.sh`, not runtime Python. The phone-facing
  WiFi listener then arrived with the app-side transport task:
  `node/transport/wifi_transport.py` (persistent TCP on
  `MESHLINK_WIFI_LISTEN`, default 10.78.0.1:7800, same 2-byte framing as
  BLE) fanned in through `node/transport/multi_transport.py`. The relay
  pipeline is unchanged; a node whose listen address can't bind — or with
  `MESHLINK_WIFI_LISTEN=off` — runs byte-identically to Phase 5, so the
  "toggle off ⇒ identical to Phase 5" demo criterion still holds. See the
  app repo's docs/phase6-app-decisions.md for the wire-protocol rationale.

- **hostapd run via the distro service** (`DAEMON_CONF` →
  `/etc/hostapd/meshlink.conf`) rather than a custom systemd unit — fewest
  moving parts on Raspberry Pi OS; config file is root-only (mode 600)
  since it embeds the passphrase.

- **macOS dev-parity AP backend (Internet Sharing).** Mirroring the BLE
  split (`node/ble` bluez vs corebluetooth), AP bring-up is now a
  platform-selected `ApProvisioner` behind `node/wifi_ap.create_ap_provisioner()`:
  `HostapdProvisioner` (drives `setup_hostapd.sh`) on the Pi,
  `InternetSharingProvisioner` on a Mac, override `MESHLINK_AP_BACKEND`. macOS
  has no hostapd, so the dev backend configures the built-in Wi-Fi card as an
  AP via SystemConfiguration's `com.apple.nat.plist` + the
  `com.apple.NetworkSharing` launchd job. It is explicitly **dev/test parity
  only** (the WiFi twin of the CoreBluetooth backend), not a deployment
  target: enabling it needs root, takes the Wi-Fi card over (dropping any
  joined network), and Apple gates the real toggle behind a GUI — so
  `start()` is best-effort (writes the SSID/passphrase so a manual toggle
  uses them, tries to kick the job, else logs exact instructions). The
  SSID self-check drops the on-air leg on Mac (a single radio can't scan for
  its own AP); `verify_consistency()` compares the deployment config against
  the plist we wrote (config ↔ live-config, phone-side for on-air).

- **`main.py` now optionally provisions the AP** via
  `MESHLINK_AP_PROVISION` (default `auto`): provisions on macOS but **not**
  on the Pi, where `setup_hostapd.sh` + systemd still own it out of band (so
  Tasks 1–2's "`main.py` untouched" property holds on the Pi unchanged).
  `on`/`off` force it. Provisioning is best-effort and never fatal — a
  missing/invalid deployment config, no root, or a failed bring-up all
  degrade to BLE + WiFi-listener (byte-identical to Phase 5), never abort
  the node.
