# Node log capture — Mac BLE session, 2026-07-04

Raw node log from a Mac run on 2026-07-04 (11:53–12:05), stored verbatim with
an analysis of what each line means and, importantly, what this capture does
and does **not** prove.

## Raw log (verbatim)

```
2026-07-04 11:53:22,017 INFO meshlink.node: meshlink-node starting — logging to /Users/darthsid/Programs/MeshLink-Node/node.log
2026-07-04 11:53:22,130 INFO meshlink.node: node up — zone_id=1, advertising MeshLink service
2026-07-04 11:53:22,178 INFO meshlink.ble: GATT service registered
2026-07-04 11:53:22,178 INFO meshlink.ble: advertising MeshLink service 4d455348-4c49-4e4b-0001-000000000001
2026-07-04 12:01:43,096 INFO meshlink.node: meshlink-node starting — logging to /Users/darthsid/Programs/MeshLink-Node/node.log
2026-07-04 12:01:43,165 INFO meshlink.node: node up — zone_id=1, advertising MeshLink service
2026-07-04 12:01:43,211 INFO meshlink.ble: GATT service registered
2026-07-04 12:01:43,212 INFO meshlink.ble: advertising MeshLink service 4d455348-4c49-4e4b-0001-000000000001
2026-07-04 12:04:47,866 INFO meshlink.ble: central connected: CF3953AA-293C-12F7-DEE3-DECAB7791AD9
2026-07-04 12:04:52,335 INFO meshlink.relay: received 144-byte packet from CF3953AA-293C-12F7-DEE3-DECAB7791AD9
2026-07-04 12:04:52,336 INFO meshlink.relay: accepted msg dc9ba74d from CF3953AA-293C-12F7-DEE3-DECAB7791AD9: 'hello'
2026-07-04 12:04:52,336 INFO meshlink.backhaul: would forward 144-byte packet to zone 3 — no backhaul yet (Phase 3)
2026-07-04 12:04:52,336 INFO meshlink.relay: relayed msg dc9ba74d from CF3953AA-293C-12F7-DEE3-DECAB7791AD9 (zone 3, ttl 5→4)
2026-07-04 12:05:09,732 INFO meshlink.relay: received 142-byte packet from CF3953AA-293C-12F7-DEE3-DECAB7791AD9
2026-07-04 12:05:09,732 INFO meshlink.relay: accepted msg 27bfcff6 from CF3953AA-293C-12F7-DEE3-DECAB7791AD9: 'yay'
2026-07-04 12:05:09,732 INFO meshlink.backhaul: would forward 142-byte packet to zone 3 — no backhaul yet (Phase 3)
2026-07-04 12:05:09,733 INFO meshlink.relay: relayed msg 27bfcff6 from CF3953AA-293C-12F7-DEE3-DECAB7791AD9 (zone 3, ttl 5→4)
```

## What each line means

- **`meshlink-node starting — logging to …node.log`** — process start. The node
  came up twice (11:53 and 12:01); the first run had no phone activity.
- **`node up — zone_id=1, advertising MeshLink service`** — this node serves
  **zone 1**. Remember this; the messages below are addressed to zone 3.
- **`GATT service registered` / `advertising MeshLink service 4d455348-…0001`**
  — the CoreBluetooth backend registered the GATT service and is advertising
  the MeshLink service UUID. **This confirms the Mac BLE port advertises.**
- **`central connected: CF3953AA-…`** — a phone (BLE central, CBCentral UUID
  `CF3953AA-293C-12F7-DEE3-DECAB7791AD9`) connected and subscribed to TX.
- **`received 144-byte packet from CF3953AA-…`** — that phone wrote a framed
  packet to the RX characteristic.
- **`accepted msg dc9ba74d from CF3953AA-…: 'hello'`** — the meshlink-core
  pipeline accepted the message (id `dc9ba74d`, payload `hello`); not a
  duplicate, not dropped.
- **`would forward 144-byte packet to zone 3 — no backhaul yet (Phase 3)`** —
  the message is addressed to **zone 3**, not this node's zone 1, so it's a
  cross-zone message. The backhaul here is the **logging stub**, which only
  says what a real backhaul *would* do.
- **`relayed msg dc9ba74d … (zone 3, ttl 5→4)`** — the node decremented TTL
  5→4 and relayed to its other local phones. (See the caveat below about how
  many phones were actually connected.)
- The `27bfcff6 / 'yay'` block at 12:05:09 is a second message, same story.

## What this capture proves

- ✅ The **Mac CoreBluetooth backend advertises** and a phone can discover,
  connect, subscribe, and write to it.
- ✅ The **relay pipeline accepts** real messages from a real phone and
  decrements TTL (5→4) — the node is on the path.

## What it does NOT prove (important caveats)

1. **Only one phone appears in this log.** There is a single
   `central connected:` line (`CF3953AA-…`) and no second one. With one phone
   connected, the "relayed msg" line had **no other local peer to send to** —
   so this capture does **not**, on its own, show the phone → node → phone
   delivery to a *second* phone. The `relayed msg` line logs regardless of how
   many peers received it.
2. **This is not the Phase3 code.** The `logging to …node.log` line and the
   `accepted msg …: 'hello'` payload line come from the **file-backed logging /
   payload-visibility** feature that lives on the `main` branch, and the
   backhaul here is the **`LoggingStubBackhaul`** ("no backhaul yet (Phase 3)")
   — not the `BatmanBackhaul` on the `Phase3` branch. So this run was made from
   a `main`-branch (Phase 2 + logging) checkout, not the Mac-backhaul work.
   On the current `Phase3` code these exact lines would differ (no "logging
   to", a real backhaul send/error instead of "would forward").
3. **Messages were addressed to zone 3, node serves zone 1** — a cross-zone
   case that on Phase3 would attempt a real backhaul send (and, with no reachable
   zone-3 node, log a dropped-send error), not the stub's "would forward".

## To capture clean two-phone Phase 2 evidence

Follow `docs/tests/` steps with **both** phones connected (expect **two**
`central connected:` lines with distinct UUIDs), send from phone A, and
confirm a `relayed msg … from <UUID-A>` line **plus** the message appearing on
phone B. To prove it went phone→node→phone and not phone→phone, stop the node
and resend: delivery must stop (phones are BLE centrals and cannot connect to
each other, so the node is the only possible relay).
