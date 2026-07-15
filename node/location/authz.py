"""Capability enforcement for node-served location (LOCATION_QUERY).

The node ENFORCES consent, it never AUTHORS it (security invariant 1):
serving a coordinate requires a token signed by the target's own long-term
Ed25519 key, verified with core's pure `capability.verify`. There is no code
path here that reads the location store without a verified token — friendship
alone, node operator say-so, or a forged token all end in the same refusal.

Refusals are silent and uniform (§8.3 style): expired token, wrong grantee,
revoked grant, rate-limited, no beacon yet, and "no such user" all produce
exactly the same observable outcome — no response. A prober cannot
distinguish "user exists but denied" from "no such user". Every refusal is
logged with a reason for the NOC.

Rate limiting: 1 query per (requester, target) per 60 s, so a friend with a
valid token still cannot poll a position into a fine-grained track — the
query side of retention invariant 3.
"""
import logging
import threading
import time
from typing import Callable, Optional

from node.core import (
    Message,
    encode_location_response,
    LocationResponsePayload,
    decode_location_query,
    decode_location_revoke,
    parse_capability_token,
    pubkey_id,
    revocation_key,
    verify_capability_token,
)
from node.directory.cache import DirectoryCache
from node.location.store import LocationStore

log = logging.getLogger("meshlink.location")

QUERY_MIN_INTERVAL_S = 60.0


class LocationAuthz:
    def __init__(
        self,
        store: LocationStore,
        directory: DirectoryCache,
        clock: Callable[[], float] = time.time,
        query_min_interval_s: float = QUERY_MIN_INTERVAL_S,
    ):
        self._store = store
        self._directory = directory
        self._clock = clock
        self._min_interval_s = query_min_interval_s
        self._lock = threading.Lock()
        # Revocation set, populated by LOCATION_REVOKE. Key = the token's
        # (issuer_pubkey_id, grantee_pubkey_id, issued_at, nonce).
        self._revoked: set[tuple] = set()
        self._last_query: dict[tuple[bytes, bytes], float] = {}

    def handle_query(self, msg: Message) -> Optional[bytes]:
        """Process a pipeline-accepted LOCATION_QUERY. Returns the
        LOCATION_RESPONSE payload (hint + sealed coordinate) to send back to
        the requester, or None for the uniform silent refusal.

        The message has already passed pipeline steps 1–7 (size/TTL/
        timestamp/dedup/rate-limit/signature/attestation) — this is the
        additional capability check after step 8 routing, only for queries
        addressed to self-as-node.
        """
        try:
            raw_token = decode_location_query(msg.payload)
            token = parse_capability_token(raw_token)
        except ValueError:
            return self._refuse(msg, "malformed query")

        requester_id = pubkey_id(msg.sender_key)
        if token.grantee_pubkey_id != requester_id:
            # The envelope sender (Ed25519-verified at step 6) must BE the
            # grantee — a stolen token is useless without the grantee's key.
            return self._refuse(msg, "sender is not the grantee")

        target = self._directory.by_ed25519_id(token.issuer_pubkey_id)
        if target is None:
            return self._refuse(msg, "issuer not in directory")
        target_ed_pub = bytes.fromhex(target["ed25519_pub"])

        now = self._clock()
        # The full pure verification: signature by the target's long-term
        # key, grantee binding, scope bit, time window (invariants 1 and 5).
        if not verify_capability_token(raw_token, target_ed_pub,
                                       msg.sender_key, int(now)):
            return self._refuse(msg, "token verification failed")

        if revocation_key(token) in self._revoked:
            return self._refuse(msg, "token revoked")

        pair = (requester_id, token.issuer_pubkey_id)
        with self._lock:
            last = self._last_query.get(pair, 0.0)
            if now - last < self._min_interval_s:
                return self._refuse(msg, "query rate limit")
            self._last_query[pair] = now

        row = self._store.get(target_ed_pub)
        if row is None:
            return self._refuse(msg, "no beacon for target")

        requester = self._directory.by_ed25519_id(requester_id)
        if requester is None:
            return self._refuse(msg, "requester not in directory")

        payload = LocationResponsePayload(
            target_pubkey_id=token.issuer_pubkey_id,
            lat_microdeg=row.lat_microdeg,
            lon_microdeg=row.lon_microdeg,
            accuracy_m=row.accuracy_m,
            beacon_age_s=self._store.beacon_age_s(row),
            zone_id=row.zone_id,
        )
        log.info("location served: target=%s requester=%s age=%ds",
                 token.issuer_pubkey_id.hex(), requester_id.hex(),
                 payload.beacon_age_s)
        # Invariant 4: sealed to the requester's Curve25519 key — the reply
        # is unreadable to a passive backhaul sniffer.
        return encode_location_response(
            payload, requester_id,
            bytes.fromhex(requester["curve25519_pub"]),
        )

    def handle_revoke(self, msg: Message) -> bool:
        """Process a pipeline-accepted LOCATION_REVOKE. Only the token's
        issuer may revoke it: the envelope sender (signature-verified) must
        hash to the revocation key's issuer_pubkey_id, else anyone could
        revoke anyone's grants."""
        try:
            payload = decode_location_revoke(msg.payload)
        except ValueError:
            log.info("malformed LOCATION_REVOKE dropped")
            return False
        if pubkey_id(msg.sender_key) != payload.issuer_pubkey_id:
            log.info("LOCATION_REVOKE sender is not the issuer — ignored")
            return False
        with self._lock:
            self._revoked.add(payload.revocation_key)
        log.info("capability revoked: issuer=%s grantee=%s",
                 payload.issuer_pubkey_id.hex(), payload.grantee_pubkey_id.hex())
        return True

    def _refuse(self, msg: Message, reason: str) -> None:
        # Uniform refusal: nothing is sent back, whatever the reason. The
        # reason exists only for the NOC log (§8.3 — silent drop, log it).
        log.info("location query refused from sender=%s: %s",
                 msg.sender_key.hex()[:16], reason)
        return None
