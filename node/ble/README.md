# node/ble — BlueZ GATT server

The node acts as a BLE **peripheral**: it advertises the MeshLink service as a
full, standard advertisement (discoverable by both iOS and Android — never the
iOS overflow area, which only applies to backgrounded iOS *peripherals*; the
node is always-on Linux hardware, which is the point of the architecture).

## Environment tested

| Item | Value |
|------|-------|
| BlueZ version | 5.66 (Raspberry Pi OS Bookworm default) |
| Python bindings | `python3-dbus` (dbus-python) + `python3-gi` (PyGObject/GLib) |
| Adapter | Pi 4B onboard (hci0); external adapters also work — first adapter exposing `GattManager1` + `LEAdvertisingManager1` is used |

## D-Bus names and paths

| Object | Path / name |
|--------|-------------|
| Bus | system bus, talking to service `org.bluez` |
| Application root (ObjectManager) | `/com/meshlink/node` |
| GATT service | `/com/meshlink/node/service0` |
| RX characteristic (write, write-without-response) | `/com/meshlink/node/service0/char_rx` |
| TX characteristic (notify) | `/com/meshlink/node/service0/char_tx` |
| LE advertisement | `/com/meshlink/node/advertisement0` |

Registered via `GattManager1.RegisterApplication` and
`LEAdvertisingManager1.RegisterAdvertisement` on the adapter. Requires root
(or a polkit rule) — the systemd unit runs as root for Phase 2.

## UUIDs

Defined in `node/config.py`, in lockstep with the app:

- Service: `4d455348-4c49-4e4b-0001-000000000001`
- RX (phone → node): `4d455348-4c49-4e4b-0002-000000000002`
- TX (node → phone): `4d455348-4c49-4e4b-0003-000000000003`

## Framing

2-byte big-endian length prefix per packet, chunked to the ATT payload size
(`node/ble/framing.py`). Reassembly is per-peer; a corrupt length prefix
(> 460 bytes, meshlink-core's MAX_PACKET) clears that peer's buffer.

## Known Phase 2 limitation — notification fan-out

BlueZ delivers a `Value` `PropertiesChanged` notification to **every**
subscribed central; a shared GATT characteristic cannot unicast. Outbound
packets therefore reach all connected phones and the phone-side pipeline's
`msg_id` dedup discards duplicates. Fine at Phase 2 scale (a handful of
phones, one node); revisit if per-peer delivery becomes a bandwidth problem.

## Verifying discovery from phones

- **Android**: nRF Connect → scan → the `MeshLink-Node` advertisement shows the
  full service UUID. Connect and the service/characteristic tree appears.
- **iOS**: LightBlue or nRF Connect for iOS → same check. iOS hides raw MAC
  addresses but the service UUID and name are visible.
