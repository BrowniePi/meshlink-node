# 3-node physical relay test — separate rooms

**Claim being validated:** three Pi nodes physically separated (different rooms,
far enough apart that they rely on the backhaul mesh, not direct BLE), form a
connected batman-adv mesh, and a message from a phone on Node A crosses the
backhaul and is delivered to a phone on a different node.

## Status

- ✅ Software path verified end-to-end over real UDP sockets (automated,
  evidence below)
- ⬜ Physical run on 3 × Pi 4B in separate rooms — **pending hardware session**
  (runbook below; same convention as `docs/demos/phase2-node-relay.md`)

## Software evidence (2026-07-03, dev machine)

`test_full_cross_node_chain_phone_to_phone` drives the exact production path —
FakeTransport (BLE radio faked) → `NodeRelay` → meshlink-core `RelayPipeline` →
`BatmanBackhaul` UDP send → second node's `BatmanBackhaul` listener → second
node's `NodeRelay`/pipeline → relay to that node's phones with TTL decremented.
Loopback sockets stand in for `bat0`; everything above the socket is production
code:

```
tests/test_batman_backhaul.py::test_forward_to_zone_delivers_to_that_nodes_listener PASSED
tests/test_batman_backhaul.py::test_broadcast_reaches_other_nodes PASSED
tests/test_batman_backhaul.py::test_unknown_zone_falls_back_to_flooding PASSED
tests/test_batman_backhaul.py::test_own_zone_forward_is_dropped_with_warning PASSED
tests/test_batman_backhaul.py::test_unreachable_node_logs_and_drops_without_crashing PASSED
tests/test_batman_backhaul.py::test_own_broadcast_echo_is_ignored PASSED
tests/test_batman_backhaul.py::test_full_cross_node_chain_phone_to_phone PASSED

============================== 7 passed in 2.05s ===============================
```

Full suite: **44 passed**. Interface abstraction held: `NodeRelay`'s routing
logic calls the same `forward_to_zone` / `broadcast_to_all_nodes` it called
against the Phase 2 stub; the only change outside `backhaul/` was one
registration line for the new receive direction (`backhaul.on_receive`,
mirroring the transport registration) plus entrypoint wiring in `main.py`.

## Physical runbook (3 × Pi 4B, 2 phones, separate rooms)

1. **Prepare each node** (i = 1, 2, 3):
   ```bash
   sudo scripts/setup_backhaul_radio.sh
   sudo MESHLINK_NODE_ID=<i> scripts/setup_batman.sh
   MESHLINK_ZONE_ID=<i> python3 -m node.main
   ```
2. **Place the nodes in different rooms/floors** — far enough apart that a
   phone near Node A cannot see Node B's BLE advertisement (verify with a
   scanner app; this is the control proving the backhaul carries the message).
3. **Confirm the mesh formed** — on every node, `batctl o` must list the other
   two as originators, and `ping 10.77.0.<other>` must succeed. Record the
   originator tables.
4. **Relay across the mesh** — connect Phone A to Node A and Phone B to Node B
   (journal shows `central connected:`), send a message addressed to Node B's
   zone from Phone A.
5. **Expected result** — Phone B displays the message. Node A's journal:
   `forwarded <n>-byte packet to zone 2 via ('10.77.0.2', 19788)`; Node B's
   journal: `relayed msg <id> from backhaul:10.77.0.1:19788 (zone 2, ttl 5→4)`.
6. **Note the cross-node hop latency** — rough wall-clock between Node A's
   `forwarded` line and Node B's `relayed` line (journal timestamps suffice;
   rigorous benchmarking is later-phase work).

## Results (fill in during the physical run)

| Check | Result |
|-------|--------|
| All 3 nodes in `batctl o` from every node | ⬜ |
| Phone A → Node A → backhaul → Node B → Phone B | ⬜ |
| Observed cross-node hop latency | ⬜ |
| RF/interference issues in multi-room setup | ⬜ (document even if none) |

Any issues found get logged as new tracker tasks, not worked around silently.
