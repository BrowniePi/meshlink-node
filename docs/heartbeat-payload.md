# Heartbeat payload (v2)

`POST {MESHLINK_BACKEND_URL}/heartbeat`, `Content-Type: application/json`,
sent by every node once per `MESHLINK_HEARTBEAT_INTERVAL_S` (default 60 s).
Fire-and-forget: the node ignores the response body; any 2xx counts as
delivered. Sender: `node/monitoring/heartbeat_sender.py`.

This is the **only** node→internet traffic, and it carries counters and
status only — never message content, sender keys, or payloads.

## Template

```json
{
  "heartbeat_version": 2,
  "node_id": "meshlink-pi-04",
  "zone_id": 3,
  "zone_name": "Main Stage",
  "sent_at": "2026-07-07T18:30:12+00:00",
  "uptime_s": 4210,

  "connected_phone_count": 5,
  "batman_peer_count": 2,

  "phones": {
    "ble_count": 3,
    "wifi_count": 2,
    "peers": ["AA:BB:CC:DD:EE:FF", "…", "wifi:10.78.0.17:52310", "wifi:10.78.0.21:49102"]
  },

  "battery": {
    "percent": 76,
    "charging": false,
    "source": "sysfs:BAT0"
  },

  "relay": {
    "received": 132,
    "accepted": 118,
    "dropped": 14,
    "relayed_to_phones": 118,
    "forwarded_cross_zone": 9,
    "broadcast_to_nodes": 3,
    "attestations_cached": 12
  },

  "system": {
    "platform": "Linux-6.6.31-v8-aarch64-with-glibc2.36",
    "cpu_temp_c": 51.2,
    "load_avg_1m": 0.42,
    "mem_used_percent": 38.5,
    "disk_used_percent": 61.0
  }
}
```

## Field notes

| Field | Type | Notes |
|---|---|---|
| `heartbeat_version` | int | Bump on any breaking shape change. v1 was the flat Phase 5 body. |
| `node_id` | string | Unique per node (`MESHLINK_NODE_ID`, default hostname). **Key node records on this.** |
| `zone_id` | int | Routing zone the node serves. A zone can have several nodes, so `(zone_id → node_id)` is one-to-many. |
| `zone_name` | string | Human label for the zone (`MESHLINK_ZONE_NAME`, default `"Zone <id>"`). Cosmetic only; identical across all nodes of a zone. |
| `sent_at` | string | ISO 8601 UTC, node's clock. Prefer the backend's receive time for online/offline logic — Pi clocks drift. |
| `uptime_s` | int | Seconds since the node process started (monotonic). A reset ⇒ the node restarted. |
| `connected_phone_count` | int | Total phones across both transports (= `phones.ble_count + phones.wifi_count`). Kept top-level for v1 compatibility. |
| `batman_peer_count` | int | Other nodes reachable over the batman-adv backhaul. Kept top-level for v1 compatibility. |
| `phones.peers` | string[] | Transport-level peer ids: BLE addresses, and `wifi:<ip>:<port>` for WiFi. Ephemeral (change on reconnect) — good for "what's connected right now", useless as stable phone identity. |
| `battery` | object \| **null** | `null` = no battery (mains-powered bench Pi, Mac mini) or read failure — don't render as 0 %. `charging` false means actively draining. `source` is `pmset` (Mac) or `sysfs:<supply>` (Pi/Linux battery HAT). |
| `relay` | object \| null | Since-boot counters (reset with `uptime_s`). `received = accepted + dropped` (+ attestation presentations). Store raw; compute rates in the dashboard from consecutive beats. |
| `system` | object | Any field may be null on a platform that can't provide it (e.g. `cpu_temp_c`/`mem_used_percent` on Mac dev nodes). |

## Backend expectations

- Any 2xx is fine (the existing test stand-in returns 201). The node never
  retries a failed beat; the next interval just sends a fresh one.
- Treat a node as offline after ~3 missed intervals (no beat for 180 s at
  the default 60 s interval).
- Accept unknown fields leniently — the node side will grow fields within
  v2 without notice; only shape-breaking changes bump the version.
