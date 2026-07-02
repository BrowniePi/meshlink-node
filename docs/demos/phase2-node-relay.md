# Phase 2 milestone demo — single-node phone relay

**Claim being validated:** two phones that are *not* in BLE range of each other,
but both in range of one Raspberry Pi node, exchange a message routed entirely
through the node. This is the demo that proves the infrastructure-anchored
architecture (node backbone), not phone-to-phone mesh.

## Status

- ✅ Software path verified end-to-end (automated, evidence below)
- ⬜ Physical run on Pi 4B + two phones — **pending hardware session** (runbook
  below; same convention as the Phase 1 demo doc in meshlink-app, where the
  two-phone run was recorded once hardware was available)

## Software evidence (2026-07-02, dev machine)

`test_full_pipeline_round_trip_through_node` drives the exact production code
path — `BleTransport` → `NodeRelay` → meshlink-core `RelayPipeline` → relay to
other peers with TTL decremented — with only the D-Bus layer faked:

```
tests/test_ble_transport.py::test_full_pipeline_round_trip_through_node PASSED
tests/test_core_parity.py::test_node_pipeline_matches_reference_vectors PASSED
tests/test_core_parity.py::test_outcome_enum_values_match_wire_strings PASSED

============================== 3 passed in 0.01s ===============================
```

Full suite: **27 passed** (framing, relay routing, backhaul stub, zone_id,
transport error containment, core parity vectors shared with the app's
`core_parity_test.dart`).

## Physical runbook (Pi 4B + iPhone + Android)

1. **Prepare the node**
   ```bash
   sudo scripts/setup-pi.sh && sudo systemctl start meshlink-node
   journalctl -u meshlink-node -f   # keep visible during the demo
   ```
   Expect: `GATT application registered`, `advertising MeshLink service 4d455348-…0001`.
2. **Confirm discovery from both platforms** — scan with nRF Connect (Android)
   and LightBlue/nRF Connect (iOS); both must show the `MeshLink-Node`
   advertisement with the full service UUID. This verifies the
   standard-advertisement requirement (no iOS overflow area).
3. **Prove the phones are out of direct range** — with the node stopped
   (`sudo systemctl stop meshlink-node`), place the phones ~40–60 m apart
   (or behind heavy walls), attempt a direct send in meshlink-app, and confirm
   it fails / no peer discovered. Record this — it's the control.
4. **Relay through the node** — start the node again (positioned roughly midway,
   both phones within ~15 m of it), let both phones connect (journal shows two
   `central connected:` lines), send a text from Phone A.
5. **Expected result** — Phone B displays the message; journal shows
   `relayed msg <id> from <phoneA> (zone 3, ttl 5→4)`. Note: the node relays
   foreign-zone traffic locally by design in Phase 2 (single-zone world), so
   the app's default `zone_id=3` works against the node's `NODE_ZONE_ID=1`;
   the journal will also show the backhaul stub's "would forward to zone 3"
   line — that is the Phase 3 seam, not an error.
6. **Capture evidence** — screen recordings of both phones + journal excerpt;
   commit them under `docs/demos/` and tick the checkbox above.

Any issues found during the physical run get logged as new tracker tasks, not
worked around silently.
