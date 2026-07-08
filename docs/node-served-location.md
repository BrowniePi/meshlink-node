# Node-Served Location under Capability Check (Phase 5 extension)

The node serves a friend's last-known coordinate directly (fast, works while
the target phone is asleep) — but only against a capability token signed by
the target's own Ed25519 key. **The node enforces consent; it never authors
it.**

## What was added

| Module | Purpose |
|---|---|
| `node/location/store.py` | Latest-coordinate-only table: one row per stable identity, overwritten per beacon. |
| `node/location/authz.py` | Capability check (core `capability.verify`), revocation set, per-(requester, target) 60 s query rate limit, uniform silent refusals. |
| `node/location/service.py` | Node-terminated routing for LOCATION (0x02) beacons, LOCATION_QUERY, LOCATION_REVOKE; signs LOCATION_RESPONSE with the node's own identity (`node_identity.json`, generated on first boot). |
| `node/directory/cache.py` | Offline-capable read-only copy of `{username, curve25519_pub, ed25519_pub}` from the backend's `/directory/sync`, refreshed at the heartbeat cadence. Public identity only — never locations. |
| `node/relay.py` hook | After the full 8-step pipeline accepts a packet, node-terminated location types are consumed instead of fanned out. Steps 1–7 are untouched: a query is still a signed, attested, rate-limited message. |

## Security invariants, node side

1. **Node never fabricates consent.** The only path to a stored coordinate is
   `authz.handle_query` behind `capability.verify` (target-signed token,
   grantee = Ed25519-verified envelope sender). Proven by
   `test_node_cannot_answer_without_token_even_for_real_friends` and the
   forged-issuer test.
2. **Stable identity never touches BLE advertising.** The identity ↔ location
   mapping (`sender_key` → row) exists only inside the node; it is built from
   the signed envelope on established sessions. Advertising still carries
   only the rotating `ephemeral_id` (Tech Ref §7.4) — nothing in this change
   touches advertising. This is what keeps the Bridgefy passive-tracking
   attack defeated.
3. **Latest-coordinate-only retention.** Storage is a plain overwrite
   (`store.update` assignment); `test_overwrite_not_append_100_beacons` fires
   100 beacons and asserts one row after each. No history container exists
   for a trail to accumulate in. Query-side: the 60 s rate limit stops a
   valid token being polled into a track.
4. **Responses sealed to the requester** (core `crypto/sealed.py`) — a
   backhaul sniffer sees only ciphertext.
5. **Revocation + expiry enforced**: LOCATION_REVOKE (issuer-verified) feeds
   the in-memory revocation set; expiry is checked in `capability.verify`.

Refusals are silent and uniform (§8.3): expired, wrong grantee, revoked,
rate-limited, unknown user, and no-beacon are indistinguishable to the
requester; reasons go to the node log for the NOC.

## Deliberate trust decision (document, don't hide)

The node *does* see current location plaintext (it decodes beacons to serve
them). This is the chosen hybrid: bounded trust in event-owned infrastructure
— retention-limited to a single latest coordinate, identity-mapping kept off
the air interface — in exchange for served-while-asleep queries. A
node-blind Model B is a possible future "high-privacy mode", deliberately
not built now.

## Behaviour notes

- LOCATION (0x02) beacons are **node-terminated** — never fanned out to the
  zone (a raw coordinate isn't broadcast material). The MLPP1 phone-ping
  telemetry remains a separate, unsigned ops path feeding heartbeats only;
  the signed LOCATION beacon is the consent-driven sharing path. Distinct
  purposes, deliberately not merged.
- Queries are answered from this node's own store. Cross-zone lookups
  (target beaconing to a different node) are out of scope for this phase —
  the query is refused identically to unknown-user.
- Config: `MESHLINK_NODE_IDENTITY`, `MESHLINK_DIRECTORY_CACHE`,
  `MESHLINK_LOCATION_QUERY_MIN_INTERVAL_S` (default 60).
