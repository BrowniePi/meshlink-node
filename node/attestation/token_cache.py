"""Ticket-bound attestation for pipeline step 7 (Phase 5).

AttestationValidator implements the validator surface meshlink-core's
check_attestation expects (present / is_attested — see
pipeline/attestation_check.py). A presented compact JWT is verified offline
against the organiser Ed25519 public key; on success the sender is cached
until the token's exp. Normal messages then pass step 7 with a cache lookup —
a JWT is never re-verified per message (the validated-sender cache is the
relay-DoS mitigation, Technical Reference §3.5).

Drop reasons match the refusal table in meshlink-backend
docs/demos/phase5-attestation-gated-relay.md.
"""
import time
from collections import OrderedDict

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class TokenCache:
    """LRU cache of validated senders: sender_key -> token exp (Unix seconds).

    Entries evict on exp, or oldest-first when capacity is reached (capacity
    mirrors the dedup cache's 10,000 — far above one node-cell's 500-1,000
    phones, so evictions only matter under a churn attack).
    """

    def __init__(self, capacity: int = 10_000):
        self._capacity = capacity
        self._entries: OrderedDict[bytes, int] = OrderedDict()

    def put(self, sender_key: bytes, exp: int) -> None:
        self._entries.pop(sender_key, None)
        self._entries[sender_key] = exp
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def is_valid(self, sender_key: bytes, now: float | None = None) -> bool:
        exp = self._entries.get(sender_key)
        if exp is None:
            return False
        if (now if now is not None else time.time()) >= exp:
            del self._entries[sender_key]
            return False
        return True


class AttestationValidator:
    """Verifies attestation-presentation tokens and answers step-7 lookups."""

    def __init__(self, organiser_pubkey_hex: str, event_id: str,
                 cache: TokenCache | None = None):
        self._key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(organiser_pubkey_hex)
        )
        self._event_id = event_id
        self._cache = cache if cache is not None else TokenCache()

    def present(self, token: bytes, sender_key: bytes) -> str | None:
        """Verify a presentation payload; cache the sender on success.

        Steps 1-6 already proved the presenter holds sender_key's private key,
        so sub == sender_key hex binds this connection to a purchased ticket
        (and rejects a stolen token replayed from another identity).
        """
        try:
            compact = token.decode("ascii")
            claims = jwt.decode(
                compact, self._key, algorithms=["EdDSA"],
                options={"require": ["sub", "eid", "exp"]},
            )
        except jwt.ExpiredSignatureError:
            return "attestation token expired"
        except jwt.exceptions.InvalidSignatureError:
            return "attestation token signature invalid"
        except (UnicodeDecodeError, jwt.exceptions.PyJWTError):
            # Not even a structurally valid EdDSA JWT (garbage payload,
            # missing claims, alg confusion, ...).
            return "attestation token malformed"
        if claims["eid"] != self._event_id:
            return "attestation token for wrong event"
        if claims["sub"] != sender_key.hex():
            return "attestation token subject mismatch"
        self._cache.put(sender_key, int(claims["exp"]))
        return None

    def is_attested(self, sender_key: bytes) -> bool:
        return self._cache.is_valid(sender_key)
