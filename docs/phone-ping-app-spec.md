# Phone telemetry ping — app-side spec (`meshlink-app`)

The **responder** half of the node's 2-minute telemetry ping. The node
(`node/monitoring/phone_ping.py`) asks each connected phone for its location and
battery; the app answers. The node folds the latest answer per phone into its
heartbeat so the organiser dashboard can see crowd location and phone health —
**without any message leaving the venue mesh** (the node never forwards these
frames on to other phones or the backend as message content; only the derived
telemetry rides the existing heartbeat).

This document is the wire contract. The node is already implemented and shipped;
the app must match it exactly. Node-side reference: `PHONE_PING_MAGIC`,
`encode_ping`, `encode_pong`, `decode` in
[node/monitoring/phone_ping.py](../node/monitoring/phone_ping.py).

## 1. Where it rides

Telemetry frames travel over the **same transport a phone is already connected
on** — BLE or WiFi — using the **identical framing** as mesh packets, so no new
channel, characteristic, or socket is needed:

| Transport | Node → phone (ping) | Phone → node (pong) |
|---|---|---|
| **BLE** | node notifies on **TX char** `4d455348-4c49-4e4b-0003-000000000003` | phone writes on **RX char** `4d455348-4c49-4e4b-0002-000000000002` |
| **WiFi** | node sends on the open TCP connection (`10.78.0.1:7800`) | phone sends back on the same connection |

Framing is **2-byte big-endian length prefix + payload**, then (BLE only)
chunked to the ATT payload size — exactly what the app's existing transports
already do for mesh packets (see [node/ble/framing.py](../node/ble/framing.py),
mirrored by `lib/transport/ble_transport.dart` / `wifi_transport.dart`). A
telemetry frame is just another framed payload; reassembly is unchanged.

## 2. Demux rule (critical)

Every reassembled frame the app receives is **either** a mesh packet **or** a
telemetry control frame. Distinguish by the first 5 bytes:

```
if frame.startsWith(bytes "MLPP1"):   // 0x4D 0x4C 0x50 0x50 0x31
    handle as telemetry (this spec) — do NOT feed to the mesh pipeline
else:
    existing mesh-packet path (unchanged)
```

A real mesh packet is ≥131 bytes starting with a random 16-byte `msg_id`, so a
false match on `MLPP1` is ~2⁻⁴⁰. This mirrors the node, which demuxes these
frames off its relay pipeline the same way. **A telemetry frame must never be
handed to the meshlink-core decode path** — it isn't a signed packet and will
(correctly) fail parsing; catch it first.

## 3. Frame formats

Both frames are `MLPP1` + a compact JSON object (UTF-8, no whitespace).

### Ping (node → phone) — the app receives this

```
MLPP1{"t":"ping","node_name":"Main Stage Node","node_lat":51.5074,"node_lon":-0.1278}
```

Its arrival **is** the request; `node_name`/`node_lat`/`node_lon` describe the
node sending it, not the phone. `node_name` is an operator-set label
(`node/config.py NODE_INFO_PATH`, default `"MeshLink Node"`); `node_lat`/
`node_lon` are the node's own fixed position, set once per deployment (not a
live GPS reading) — both are **omitted entirely** from the frame if the
operator hasn't configured a location. Treat any unknown extra keys leniently
(the node may add fields within this version), and don't assume these three
keys are always present.

### Pong (phone → node) — the app sends this

```
MLPP1{"t":"pong","lat":51.5074,"lon":-0.1278,"battery":84,"charging":true}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `t` | string | **yes** | Must be `"pong"`. |
| `lat` | number \| null | yes (nullable) | WGS-84 latitude. `null` if location is unavailable/denied — send the pong anyway. |
| `lon` | number \| null | yes (nullable) | WGS-84 longitude. `null` under the same conditions as `lat`. |
| `battery` | int \| null | yes (nullable) | Phone battery percent 0–100. `null` if the platform won't report it. |
| `charging` | bool | optional | Omit if unknown; the node stores `null` when absent. |

The node parses leniently: missing `lat`/`lon`/`battery` are stored as `null`; a
frame that isn't valid JSON or lacks `"t"` is dropped silently. Keep the object
small — it shares the phone's radio with real traffic.

Dart encode reference:

```dart
import 'dart:convert';
import 'dart:typed_data';

final _magic = Uint8List.fromList('MLPP1'.codeUnits);

Uint8List encodePong({double? lat, double? lon, int? battery, bool? charging}) {
  final body = <String, dynamic>{'t': 'pong', 'lat': lat, 'lon': lon, 'battery': battery};
  if (charging != null) body['charging'] = charging;
  return Uint8List.fromList([..._magic, ...utf8.encode(jsonEncode(body))]);
}

bool isPing(Uint8List frame) =>
    frame.length >= 5 &&
    frame[0] == 0x4D && frame[1] == 0x4C && frame[2] == 0x50 &&
    frame[3] == 0x50 && frame[4] == 0x31 &&
    (jsonDecode(utf8.decode(frame.sublist(5)))['t'] == 'ping');
```

## 4. App responsibilities

On receiving a ping frame:

1. **Read battery** — `battery_plus` (`Battery().batteryLevel`,
   `Battery().batteryState` for charging).
2. **Read location** — `geolocator` `getCurrentPosition(desiredAccuracy:
   LocationAccuracy.high)` for a precise (street/stage-level) fix; use
   `getLastKnownPosition()` only as an immediate fallback if a fresh fix can't
   be obtained in time. If permission is denied or location services are off,
   use `null` for `lat`/`lon`.
3. **Send the pong** back on the **same transport** the ping arrived on, framed
   identically to a mesh packet.

The reply should be prompt but need not be synchronous — resolving location can
take a moment; answer within a few seconds. If a second ping arrives while one
is in flight, it's fine to coalesce (answer once).

## 5. Privacy & permissions

- **Ask for location permission in context**, explaining it powers the
  organiser's live crowd map; a user who declines still functions on the mesh —
  the app simply reports `lat: null, lon: null`. Never block messaging on it.
- Request **precise, "while in use"** location. Do not request background
  location for this feature — the node only pings while the phone is connected
  and in the foreground.
- The app **stores nothing** — each pong is computed fresh on demand. The node
  keeps only the latest report per phone and ages it out after 3 missed pings.
- Location and battery leave the phone only over the local mesh link to the
  node; they reach the internet only as aggregated heartbeat telemetry, never as
  message content.

## 6. Cadence & constants

| Constant | Value | Source |
|---|---|---|
| Magic prefix | `MLPP1` (5 bytes) | `PHONE_PING_MAGIC` |
| Ping interval | 90 s (node-driven; configurable via `MESHLINK_PHONE_PING_INTERVAL_S`) | node config |
| Connect ping | An extra ping fires 3 s after a phone connects (BLE or WiFi), on top of the periodic sweep — so the first report isn't delayed up to a full interval, but the app still gets a moment to settle after the link comes up. Configurable via `MESHLINK_PHONE_PING_CONNECT_DELAY_S`. | `node/monitoring/phone_ping.py PhonePingService.on_peer_connected`, wired via `transport.on_connect` in `node/main.py` |
| Report TTL (node) | 3 × interval | node |
| Max frame | 460 bytes | `node/ble/framing.py` `MAX_FRAME` |

The app never initiates — it only answers pings, so the app carries **no timer**
for this feature. Cadence is entirely the node's.

## 7. Acceptance checklist

- [ ] Reassembled frames starting with `MLPP1` are routed to the telemetry
      handler, never to the mesh-packet decoder (both BLE and WiFi paths).
- [ ] A `{"t":"ping"}` frame triggers exactly one pong on the same transport.
- [ ] Pong carries real `battery` and (when permitted) `lat`/`lon`; denied
      location yields `lat:null,lon:null` and the pong is still sent.
- [ ] Pong is length-prefixed + (BLE) chunked identically to a mesh packet.
- [ ] Declining location permission never blocks messaging.
- [ ] Round-trips against a live node: node logs
      `phone-ping report from <peer>: lat=… lon=… battery=…` and the value
      appears in the next heartbeat's `phone_telemetry.reports[]`
      (see [docs/heartbeat-payload.md](heartbeat-payload.md)).
