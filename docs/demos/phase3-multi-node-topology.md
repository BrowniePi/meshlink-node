# Phase 3 milestone demo — 3-node topology relay

**Claim being validated:** a message travels Phone A → BLE → Node A →
batman-adv backhaul → Node B → BLE → Phone B across physically separated
nodes. This is the first demo structurally identical to the eventual festival
deployment — the same chain, at 3-node/2-phone scale instead of
50-node/50,000-phone scale.

## Why this milestone matters (vs. the Project Overview thesis)

The architectural thesis is that phone-to-phone mesh alone cannot work at mass
events — iOS's overflow-area constraint makes backgrounded iPhones invisible
to Android, and battery limits make phones unreliable relays — so MeshLink
anchors the mesh in **infrastructure**: always-on Linux nodes that phones
connect *to*, joined by a WiFi backhaul phones never touch. Phases 1–2 proved
the phone↔node edge. Phase 3 proves the middle: nodes relaying between each
other over 802.11s/batman-adv with zero involvement from any phone radio. With
this demo, every structural element of the festival deployment exists — later
phases harden it (security, backend, zones, scale) rather than add new hops to
the chain.

## Status

- ✅ Full chain verified in software, only the BLE radio faked (evidence below)
- ⬜ Physical run: 3 Pis in different rooms, 2 phones — **pending hardware
  session** (procedure: `docs/tests/3-node-relay-test.md`; capture per-hop
  evidence listed below)

## Software evidence (2026-07-03, dev machine)

Each hop of the chain, as exercised by `tests/test_batman_backhaul.py`
(`test_full_cross_node_chain_phone_to_phone` drives the whole chain in one
test over real UDP sockets):

| Hop | Mechanism | Evidence |
|-----|-----------|----------|
| Phone A → Node A | BLE GATT write → `NodeRelay` pipeline | Phase 2 suite (`test_ble_transport.py`, unchanged) |
| Node A → zone lookup | `static_zone_table` maps zone 2 → Node B | `test_static_zone_table.py` |
| Node A → Node B | `BatmanBackhaul.forward_to_zone` UDP unicast | `test_forward_to_zone_delivers_to_that_nodes_listener` |
| Node B → pipeline | listener → same relay pipeline as BLE traffic | `test_full_cross_node_chain_phone_to_phone` |
| Node B → Phone B | relay to connected peers, TTL 5→4 | `test_full_cross_node_chain_phone_to_phone` |

```
============================== 44 passed in 2.22s ==============================
```

Also verified: broadcast (zone `0xFFFF`) floods all nodes; unknown zone falls
back to flooding (Technical Reference §5.2); an unreachable node is logged and
the packet dropped without crashing the relay serving local phones.

## Evidence to capture during the physical run

1. Recording/screenshots of Phone A sending and Phone B receiving.
2. Journal excerpts from both nodes showing the hop timing:
   Node A `forwarded … to zone 2 via ('10.77.0.2', 19788)` → Node B
   `relayed msg <id> from backhaul:10.77.0.1:19788`.
3. `batctl o` output from each node proving the mesh topology (and that the
   nodes are multi-hop-capable, not just pairwise).
4. The BLE-range control: Phone A cannot see Node B directly.

Any issues found get logged as new tracker tasks. When the physical run is
done, tick the checkbox above and attach the evidence under `docs/demos/`.
