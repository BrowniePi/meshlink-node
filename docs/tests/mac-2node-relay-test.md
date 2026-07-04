# 2-node Mac relay test — LAN backhaul

**Claim being validated:** two Mac nodes on the same LAN, each serving a
different zone, relay a message across the backhaul from a phone on Node A to
a phone on Node B — the same cross-zone path as the Pi mesh, but with the
backhaul carried over ordinary LAN UDP instead of batman-adv (a Mac can't
join the 802.11s mesh).

## Status

- ✅ Software path verified end-to-end over real UDP sockets (automated,
  same `test_full_cross_node_chain_phone_to_phone` path as the Pi test)
- ✅ Env-override routing verified: `MESHLINK_ZONE_TABLE` drives
  `BatmanBackhaul` to LAN/loopback endpoints instead of `10.77.0.x`
- ⬜ Physical run on 2 × Mac + 2 phones on a LAN — **pending hardware session**
  (runbook below)

## Why this works without batman-adv

The backhaul is plain UDP; only the *addressing* was Linux/mesh-specific. On
Mac the node reads three env overrides (`MESHLINK_ZONE_TABLE`,
`MESHLINK_BACKHAUL_BROADCAST_ADDR`, `MESHLINK_BACKHAUL_PORT`) and skips the
`iw`/802.11s radio check. Everything above the socket — CoreBluetooth BLE,
framing, dedup, `NodeRelay`, the meshlink-core `RelayPipeline` — is identical
to the Pi. Echo suppression still holds because each node's own LAN IP is in
the zone table, so its own subnet broadcast is recognised and skipped.

## Software evidence (2026-07-04, dev machine)

`tests/test_backhaul_config.py` pins the override parsing and that a parsed
table reaches `BatmanBackhaul` routing; `test_batman_backhaul.py` drives the
full phone→backhaul→phone chain over loopback sockets (BLE faked). Full
suite: **54 passed**. A manual two-node loopback run (zones 1/2 on ports
19788/19789, wired only via `MESHLINK_ZONE_TABLE`) delivered a zone-2 packet
from Node A's phone to Node B's phone with TTL 5→4.

## Physical runbook (2 × Mac, 2 phones, one LAN)

1. **Both Macs on the same LAN/subnet.** Note each Mac's IP
   (`ipconfig getifaddr en0`). Call them `IP_A` (zone 1) and `IP_B` (zone 2).
   Disable any VPN (utun) that could shadow the LAN interface.

2. **Per-Mac setup** (once):
   ```bash
   git clone --recurse-submodules https://github.com/BrowniePi/meshlink-node.git
   cd meshlink-node
   python3 -m venv .venv && source .venv/bin/activate
   pip install pyobjc-framework-CoreBluetooth
   ```

3. **Launch — same zone list on both, different zone id:**
   ```bash
   # Mac A (zone 1):
   ./scripts/run-mac-node.sh 1 <IP_A> <IP_B>
   # Mac B (zone 2):
   ./scripts/run-mac-node.sh 2 <IP_A> <IP_B>
   ```
   Grant the **Bluetooth permission** prompt for your terminal on first run
   (until then the node starts but never advertises). If the nodes can't reach
   each other, allow incoming connections for `python3` in the macOS firewall
   (System Settings ▸ Network ▸ Firewall) or turn it off for the test.

4. **Confirm reachability** before involving phones: from Mac A,
   `nc -uz <IP_B> 19788` should not error, and each node's log shows
   `backhaul listening on udp/19788`.

5. **Relay across the LAN** — connect Phone A to Mac A and Phone B to Mac B
   (log shows a central subscribing to the TX characteristic), then send a
   message addressed to **zone 2** from Phone A.

6. **Expected result** — Phone B displays the message. Mac A's log:
   `forwarded <n>-byte packet to zone 2 via ('<IP_B>', 19788)`; Mac B's log:
   a `backhaul:<IP_A>:...` receive followed by `relayed msg … (zone 2, ttl 5→4)`
   and a send to Phone B. Record both logs as evidence.

### Notes / gotchas

- Two phones each connect to a **different** Mac — that's the point (cross-zone
  hop). If a phone is in BLE range of both Macs, keep them far enough apart (or
  forget the wrong node) so each phone connects to its intended node.
- `MESHLINK_ZONE_TABLE` argument order **is** the zone→IP mapping; the launch
  script warns if this Mac's own IP doesn't match the zone id you gave it.
- This proves the software path across two physical machines. It does **not**
  cover batman-adv multi-hop self-healing — that remains a Pi-only concern
  (`docs/tests/3-node-relay-test.md`).
