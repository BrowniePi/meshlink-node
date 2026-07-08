# meshlink-node

MeshLink Raspberry Pi node software (Phase 3 — "Multi-Node Backhaul").

The node runs on a Raspberry Pi 4 as a **BLE GATT peripheral** that phones connect
outbound to. It imports the shared `meshlink-core` relay pipeline (second consumer
after `meshlink-app`) and relays messages between BLE-connected phones — and, since
Phase 3, between nodes: cross-zone messages travel over a batman-adv WiFi mesh
(802.11s on a second radio) to the node serving the destination zone.

Phase 3 scope: **3 nodes, hand-wired zones**. Each node is assigned one zone
(`MESHLINK_ZONE_ID`) and a static table maps zones to node IPs — the dynamic
zone-routing table arrives in Phase 7.

## Repository layout

```
node/            main package
  ble/           GATT server (peripheral role): shared base + per-platform
                 backends (BlueZ on Linux/Pi, CoreBluetooth on macOS)
  transport/     node-side Transport adapters (BLE + WiFi), fanned in via
                 multi_transport (same interface as the app's)
  wifi_ap/       phone-facing WiFi AP (Phase 6): deployment SSID config +
                 provisioner backends (hostapd on Pi, Internet Sharing on macOS)
  backhaul/      node-to-node backhaul (batman-adv UDP + static zone table)
  core/          import shim for the vendored meshlink-core package
  relay.py       wires transport → meshlink-core pipeline → peers/backhaul
  config.py      node configuration (per-node zone_id, backhaul port)
  main.py        entrypoint
scripts/         Pi setup helpers (BLE, backhaul radio, batman-adv) + systemd unit
vendor/          meshlink-core git submodule (pinned)
tests/           unit tests (runnable on a dev machine, no BlueZ needed)
docs/demos/      milestone demo evidence
docs/tests/      physical test runbooks and results
```

## Requirements

- Raspberry Pi 4B running Raspberry Pi OS (Bookworm, 64-bit recommended)
- Python 3.10+ (Bookworm ships 3.11)
- BlueZ 5.66+ (Bookworm default) with the D-Bus API enabled (default)
- System packages: `bluez`, `python3-dbus`, `python3-gi`

## Setup on the Pi

```bash
sudo apt update
sudo apt install -y bluez python3-dbus python3-gi git python3-pip
git clone --recurse-submodules https://github.com/BrowniePi/meshlink-node.git
cd meshlink-node
```

`dbus-python` and `PyGObject` are used from the system packages (building them
via pip on the Pi requires large native toolchains for no benefit).

Run the node in the foreground:

```bash
python3 -m node.main
```

### Attestation (Phase 5)

Since Phase 5, pipeline step 7 enforces ticket-bound attestation tokens against
a running `meshlink-backend` — set the event ID to match whatever the backend
issued tokens for (defaults to `test-event-001`), and point at the backend if
it's not on `127.0.0.1:8000`:

```bash
MESHLINK_EVENT_ID=meshlink-demo \
MESHLINK_BACKEND_URL=http://127.0.0.1:8000 \
python3 -m node.main
```

The node fetches and caches the organiser's public key from the backend once
at boot (`GET /attestation/public-key`); every token verification after that
is fully offline. `MESHLINK_ORGANISER_PUBKEY` (64 hex chars) skips that fetch
entirely — useful for air-gapped bench setups or tests, but must be the real
key the backend signs tokens with, or every presentation is rejected.

### Friends & node-served location (Friendship branch)

The node terminates `LOCATION` beacons and `LOCATION_QUERY` messages after
the full 8-step pipeline accept: it keeps each sharer's **latest coordinate
only** (overwrite, never append), and answers a query only when it carries a
capability token signed by the target's own key — verified against the user
directory the node syncs from the backend (offline-capable disk cache, so a
backend outage doesn't break serving). Refusals are silent and observably
identical whatever the reason. See `docs/node-served-location.md`.

No new required config — the same two variables as attestation cover it:

```bash
MESHLINK_EVENT_ID=meshlink-demo \
MESHLINK_BACKEND_URL=http://192.168.1.14:8000 \
python3 -m node.main
```

(`192.168.1.14` = the machine running `meshlink-backend`; on that machine
itself `127.0.0.1:8000` is fine. The event id must match the app builds.)

Optional knobs:

| Variable | Default | Meaning |
|---|---|---|
| `MESHLINK_NODE_IDENTITY` | `node_identity.json` | Node's own Ed25519 keypair for signing LOCATION_RESPONSEs (created on first boot) |
| `MESHLINK_DIRECTORY_CACHE` | `directory_cache.json` | On-disk user directory cache, refreshed from `GET /directory/sync` at heartbeat cadence |
| `MESHLINK_LOCATION_QUERY_MIN_INTERVAL_S` | `60` | Per-(requester, target) query rate limit |

### Phone-facing WiFi AP (Phase 6)

Since Phase 6, phones can reach the node over **WiFi** as well as BLE. Two
independent pieces:

1. **The listener** — a persistent TCP server (`node/transport/wifi_transport.py`)
   fanned in alongside BLE, on by default (`MESHLINK_WIFI_LISTEN`, default
   `10.78.0.1:7800`; set `off` to disable). On a machine without the AP
   interface it simply fails to bind and the node runs BLE-only — byte-identical
   to Phase 5.
2. **The AP** — a radio put into AP mode broadcasting the deployment-wide SSID
   so phones can join. On the Pi this is system configuration run **out of
   band** (idempotent, safe on every boot):

```bash
# One-time: push the deployment-wide SSID/passphrase, then bring the AP up
sudo install -m 600 wifi_deployment.conf /etc/meshlink/wifi_deployment.conf
sudo scripts/setup_hostapd.sh          # renders hostapd.conf + starts the AP
scripts/verify_ssid_consistency.sh     # self-check: config ↔ hostapd ↔ on-air
```

Then run the node as usual — it now serves phones on **both** transports:

```bash
MESHLINK_EVENT_ID=meshlink-demo \
MESHLINK_BACKEND_URL=http://127.0.0.1:8000 \
python3 -m node.main
```

On the Pi, `main.py` deliberately leaves the AP to `setup_hostapd.sh` +
systemd (`MESHLINK_AP_PROVISION` defaults to `auto` = provision on macOS only;
set `on` to make the node process drive hostapd itself, `off` to skip). The
full fleet procedure — pushing one SSID to every node and updating it later —
is in `docs/wifi-ap-deployment.md`.

### Run on boot (systemd)

```bash
sudo cp scripts/meshlink-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshlink-node
journalctl -u meshlink-node -f   # follow logs
```

Or use the helper: `sudo scripts/setup-pi.sh` (installs packages + service).

### Backhaul (Phase 3, 3-node mesh)

Each node needs a second WiFi radio (USB adapter with 802.11s support); the
onboard radio stays on phone-facing duties. Per node:

```bash
sudo scripts/setup_backhaul_radio.sh              # 802.11s mesh mode, 5 GHz ch 149
sudo MESHLINK_NODE_ID=1 scripts/setup_batman.sh   # bat0 up as 10.77.0.1 (node 1)
MESHLINK_ZONE_ID=1 python3 -m node.main           # node 1 serves zone 1
```

Use node/zone 2 and 3 on the other Pis (zone N ↔ `10.77.0.N`, per
`node/backhaul/static_zone_table.py`). Verify the mesh with `batctl o` and
`ping 10.77.0.<other-id>`. Both scripts are idempotent — safe to re-run on boot.

## Running on macOS (development)

The node also runs on a Mac for development, using a CoreBluetooth backend
behind the same GATT server abstraction (`node/ble/base.py`). Platform-neutral
logic — framing, peer tracking, transport, relay — is identical on both
platforms; only the radio plumbing differs (`node/ble/bluez.py` vs
`node/ble/corebluetooth.py`), selected automatically by platform
(override with `MESHLINK_BLE_BACKEND=bluez|corebluetooth`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pyobjc-framework-CoreBluetooth
PYTHONPATH=.:vendor/meshlink-core python3 -m node.main
```

macOS-specific behaviour:

- The first run triggers the system **Bluetooth permission prompt** for the
  hosting app (Terminal, iTerm, VS Code). Until granted, the node starts but
  never reaches powered-on/advertising.
- CoreBluetooth has no peripheral-side connect/disconnect events; peer
  presence is tracked via TX-characteristic subscribe/unsubscribe, and
  `peer_id` is the CBCentral identifier UUID rather than a device path.

#### Phone-facing WiFi AP on macOS

macOS has no hostapd, so the phone-facing AP uses **Internet Sharing** through
the same `create_ap_provisioner()` abstraction that picks hostapd on the Pi
(override `MESHLINK_AP_BACKEND=hostapd|internet_sharing`), mirroring the BLE
backend split. This is **dev/test parity only**, not a deployment target:
enabling it needs root and takes the Wi-Fi card over as an AP (dropping any
network the Mac is joined to), and Apple gates the real toggle behind a GUI.

```bash
# Local deployment config (a Mac has no /etc/meshlink):
cat > wifi_deployment.conf <<'EOF'
ssid=MeshLink-Network
passphrase=venue-secret-2026
EOF

# main.py auto-provisions the AP on macOS. Run as root (via the venv's python,
# so PyObjC is on hand) so it can configure Internet Sharing:
sudo -E PYTHONPATH=.:vendor/meshlink-core \
  MESHLINK_WIFI_DEPLOYMENT_CONF=$PWD/wifi_deployment.conf \
  MESHLINK_WIFI_LISTEN=0.0.0.0:7800 \
  MESHLINK_EVENT_ID=meshlink-demo \
  MESHLINK_BACKEND_URL=http://127.0.0.1:8000 \
  .venv/bin/python -m node.main
```

**Why `MESHLINK_WIFI_LISTEN=0.0.0.0:7800` on macOS:** the listener defaults to
the Pi's hostapd subnet (`10.78.0.1`), but macOS Internet Sharing stands up its
own bridge interface on a *different* subnet (commonly `192.168.2.x` or
`192.168.64.x`). Binding `0.0.0.0` accepts on whatever subnet Sharing picked —
otherwise the listener can't bind and disables itself (`Errno 49, Can't assign
requested address`) even though the AP is up. Find the Mac's address on the
phone-facing net with `ifconfig | grep 'inet 192.168'` (the `bridgeNNN`
interface); that IP is what the app's `MESHLINK_WIFI_NODE_HOST` must target.

Without `sudo` the node won't touch networking — it logs the exact System
Settings steps and still serves BLE. If the phone can't see the SSID even under
root, enable **System Settings › General › Sharing › Internet Sharing** (share
to Wi-Fi) by hand — the SSID/passphrase are already configured for you. On-air
SSID verification is phone-side on macOS (a single radio can't scan for its own
AP). Set `MESHLINK_AP_PROVISION=off` to skip AP bring-up entirely. See
`docs/wifi-ap-deployment.md`.

#### Backhaul between Mac dev nodes

The batman-adv radio (`iw`, 802.11s, `bat0`) is Linux-only, so a Mac node
can't join the mesh — but the backhaul itself is plain UDP. Point it at real
LAN IPs or loopback ports instead of the `10.77.0.x` mesh scheme with two env
vars (same override pattern as `MESHLINK_BLE_BACKEND`); the radio check is
skipped automatically when a zone table is supplied:

```bash
# Two nodes on one Mac, over loopback (distinct ports):
MESHLINK_ZONE_ID=1 MESHLINK_BACKHAUL_PORT=19788 \
  MESHLINK_ZONE_TABLE="1=127.0.0.1:19788,2=127.0.0.1:19789" \
  python3 -m node.main
MESHLINK_ZONE_ID=2 MESHLINK_BACKHAUL_PORT=19789 \
  MESHLINK_ZONE_TABLE="1=127.0.0.1:19788,2=127.0.0.1:19789" \
  python3 -m node.main

# Nodes on separate Macs on a LAN (default port, real broadcast):
MESHLINK_ZONE_ID=1 \
  MESHLINK_ZONE_TABLE="1=192.168.1.10,2=192.168.1.11" \
  MESHLINK_BACKHAUL_BROADCAST_ADDR="192.168.1.255" \
  python3 -m node.main
```

`MESHLINK_ZONE_TABLE` entries are `zone=host` (default port) or
`zone=host:port`. Everything above the socket — framing, dedup, the relay
pipeline — is the same code the Pi mesh runs.

For a two-Mac LAN test, `scripts/run-mac-node.sh` wires these env vars for you
(auto-detects the local IP, derives the `/24` broadcast, warns on a zone/IP
mismatch). Run the same zone list on each Mac with its own zone id:

```bash
# Mac A (zone 1) and Mac B (zone 2), <IP_A>/<IP_B> = each Mac's LAN IP:
./scripts/run-mac-node.sh 1 <IP_A> <IP_B>   # on Mac A
./scripts/run-mac-node.sh 2 <IP_A> <IP_B>   # on Mac B
```

Full runbook (phones, firewall, expected logs): `docs/tests/mac-2node-relay-test.md`.

## meshlink-core dependency

`meshlink-core` is consumed as a **pinned git submodule** at `vendor/meshlink-core`,
exposed to node code through the `node/core` import shim — no relay-pipeline logic
is copied into this repo.

Why not `pip install git+…`? `meshlink-core`'s `pyproject.toml` has no build-system
configuration and uses a flat multi-package layout (`pipeline/`, `transport/`,
`routing/`), which setuptools refuses to auto-discover. Phase 2 explicitly consumes
core *without changing it*, so the submodule pin is the integration mechanism until
core gains packaging metadata. This mirrors the app-side approach in spirit: the app
(Dart) re-implemented against the shared spec; the node (Python) imports the reference
implementation directly.

Update the pin deliberately:

```bash
git -C vendor/meshlink-core fetch origin && git -C vendor/meshlink-core checkout origin/main
git add vendor/meshlink-core && git commit -m "chore: bump meshlink-core pin"
```

## Tests

Tests run on any dev machine — BlueZ/D-Bus is faked, only the pure-Python paths
are exercised:

```bash
python3 -m pip install pytest   # or: apt install python3-pytest
python3 -m pytest
```

## BLE GATT layout

Must stay in lockstep with `meshlink-app` (`lib/transport/ble_transport.dart`):

| UUID | Role |
|------|------|
| `4d455348-4c49-4e4b-0001-000000000001` | MeshLink service (advertised) |
| `4d455348-4c49-4e4b-0002-000000000002` | RX — phones write inbound packets |
| `4d455348-4c49-4e4b-0003-000000000003` | TX — node notifies outbound packets |

Packets are framed with a 2-byte big-endian length prefix and chunked to the
negotiated ATT MTU. See `node/ble/README.md` for backend specifics.
