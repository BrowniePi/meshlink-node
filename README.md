# meshlink-node

MeshLink Raspberry Pi node software (Phase 2 — "The Node Enters").

The node runs on a Raspberry Pi 4 as a **BLE GATT peripheral** that phones connect
outbound to. It imports the shared `meshlink-core` relay pipeline (second consumer
after `meshlink-app`) and relays messages between BLE-connected phones — two phones
that cannot reach each other directly can exchange messages through the node.

Phase 2 scope: **one node, no mesh**. Node-to-node backhaul (batman-adv, Phase 3)
exists only as a stub interface in `node/backhaul/`.

## Repository layout

```
node/            main package
  ble/           GATT server (peripheral role): shared base + per-platform
                 backends (BlueZ on Linux/Pi, CoreBluetooth on macOS)
  transport/     node-side Transport adapter (same interface as the app's)
  backhaul/      node-to-node interface stub (implemented in Phase 3)
  core/          import shim for the vendored meshlink-core package
  relay.py       wires transport → meshlink-core pipeline → peers
  config.py      node configuration (hardcoded zone_id for Phase 2)
  main.py        entrypoint
scripts/         Pi setup helper + systemd unit
vendor/          meshlink-core git submodule (pinned)
tests/           unit tests (runnable on a dev machine, no BlueZ needed)
docs/demos/      milestone demo evidence
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
