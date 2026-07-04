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
  transport/     node-side Transport adapter (same interface as the app's)
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
