# Phase 4 parity reconciliation — meshlink-core (Python) ↔ meshlink-app (Dart)

Written for whoever implements Phase 4 in the Python core (`pipeline/*.py`
in this repo, consumed by meshlink-node). The Dart side (`lib/core/*.dart`)
already has its Phase 4 implementation; this document says what
`test/fixtures/parity_vectors.json` needs from the Python rewrite to stay
valid, and what does *not* need to match.

Status as of writing: `pipeline/*.py` is still Phase 0/1 stubs —
`signature_check.py` always returns `None`, `dedup_check.py` is a plain
set, `rate_limit_check.py` always passes, and `pipeline/message.py` has no
`signed_region()` (the signature docstring still describes the naive
`bytes[0:75+payload_len]`). `test/core_parity_test.dart` on the Dart side
explicitly runs with `verifySignatures: false` because of this — see the
TODO left there pointing at this file.

## Must match exactly (wire/verification-affecting)

1. **`signed_region()`** — the important one. Must exclude `ttl` (offset
   68) and `spray_L` (offset 69):
   `raw[0:68] ‖ raw[70 : 75 + payload_len]`.
   Confirmed with the project owner 2026-07-04 (see `PHASE4_CHANGES.md`).
   Those two bytes are rewritten by every relay hop (ttl decremented,
   spray_L binary-split), and only the originating sender holds the
   private key — so signing over the literal spec range would make
   verification succeed only for direct (1-hop) delivery. If Python signs
   over the old literal range while Dart excludes ttl/spray_L (or vice
   versa), a Python node relaying for a Dart node — or the reverse — will
   fail signature verification at hop 2+. This is exactly the bug
   `PHASE4_CHANGES.md` describes being caught by the socket-based sim
   harness, not by unit tests, because no unit test modeled a genuine
   multi-hop relay path with real signatures. Implement Dart's
   `signedRegion()` (`lib/core/message.dart`) as the reference.

2. **Rate-limit and dedup parameters** — confirmed values, not free
   choices:
   - Rate limit: N = 10 messages per 10 s window; 3 consecutive violations
     → 60 s ban; banned messages don't touch the window; a pass resets the
     violation streak; a message exactly `WINDOW_SECONDS` old still counts
     inside the window (evict when age strictly `>` window).
   - Dedup: Bloom filter + LRU, ~1% FPR, 10,000 capacity, 10-min TTL,
     rebuild the Bloom filter after 1,000 evictions accumulate (10% of
     capacity), filter sized `capacity + 2 × rebuild_threshold`. An entry
     exactly at the 10-min TTL is still a duplicate (evict when age
     strictly `>` TTL).

3. **`"duplicate: msg_id already seen"`** — the dedup drop-reason string.
   Dart already uses this exact string and the existing `duplicate_msg_id`
   vector already asserts it. Keep it verbatim through the Bloom/LRU
   rewrite.

4. **`"invalid signature"`** — recommended exact string for the
   forged-signature drop reason, so a shared vector can assert it
   character-for-character. Dart's `signature_check.dart` already returns
   this.

## Does NOT need to match

- **msg_id realism.** Neither pipeline verifies msg_id derivation — it's
  an opaque dedup key on both sides (documented in both `message.dart` and
  `message.py`). The generator can keep hardcoding arbitrary `msg_id`
  bytes for pipeline-parity vectors; no need to route it through BLAKE3.

- **Rate-limit drop-reason wording.** Dart's text (`"rate limited: …"`) is
  local log/UI text, not wire data — see `PHASE4_CHANGES.md`. Recommend
  **not** adding a rate-limit vector to the shared fixture at all; test
  that check's wording independently in each language's own unit tests.

- **No shared key material.** Ed25519 verification is self-contained per
  packet: signature + sender_key (embedded in the packet) + signed bytes.
  The generator can sign with its own freshly generated, never-committed
  Ed25519 keypair — Dart doesn't need that private key or even need to
  know it exists. Nothing needs to be coordinated across repos here.

## Steps for the Python-side rewrite

1. Add `signed_region(raw, payload_len)` to `pipeline/message.py` with the
   exact byte range above.
2. Implement real `signature_check.py` (PyNaCl), `dedup_check.py`
   (pybloom-live + LRU), `rate_limit_check.py` per the confirmed
   parameters above.
3. Update `tests/helpers.build_packet` (or the generator directly) to
   actually sign packets: generate an ephemeral test keypair at generation
   time, sign over `signed_region()`, embed the real public key as
   `sender_key`.
4. Regenerate `test/fixtures/parity_vectors.json`
   (`python tool/gen_parity_vectors.py > test/fixtures/parity_vectors.json`).
   The existing malformed-packet cases (`too_small`, `too_large`,
   `length_mismatch`) don't need real signatures — they're rejected
   pre-signature-check regardless.
5. Optionally add:
   - `forged_signature` — sign honestly, then corrupt one byte; assert
     `outcome: "drop"`, `drop_reason: "invalid signature"`.
   - `relay_ttl_spray_rewrite` — sign, then flip the `ttl`/`spray_L` bytes
     in the fixture entry before recording it; assert `outcome: "deliver"`.
     This is the multi-hop case the signed-region bug hid from unit tests,
     so it's worth locking in as a regression vector.

## Follow-up required on the Dart side (tracked, not yet done)

Once the regenerated fixture lands with real signatures, flip
`verifySignatures: false` → `true` (or drop the override — `true` is the
default) in `test/core_parity_test.dart`. Doing this before the fixture
carries real signatures would fail every non-error vector, since Ed25519
verification would run against garbage/zeroed signature bytes — that's
why it hasn't been flipped yet.
