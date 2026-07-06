"""Task 5: real attestation validation at pipeline step 7.

Tokens here are real EdDSA compact JWTs signed with a test organiser key —
the same shape meshlink-backend issues — verified by meshlink-core's native
AttestationCache. Presentation itself is a node-level concept (NodeRelay
intercepts msg_type MSG_TYPE_ATTESTATION_PRESENT before the attestation-gated
pipeline, since a presenter isn't attested yet): meshlink-core only knows
how to validate a token once it's handed one out of band.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from node.attestation import MSG_TYPE_ATTESTATION_PRESENT
from node.core import AttestationCache, Outcome, RelayPipeline
from node.relay import BROADCAST_ZONE, NodeRelay
from tests.helpers import FakeTransport, build_packet
from tests.test_relay import RecordingBackhaul

EVENT_ID = "test-event-001"

ORGANISER_KEY = Ed25519PrivateKey.generate()
ORGANISER_PUBKEY = ORGANISER_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

# The identity every helper-built packet is signed with (vendored core's
# TEST_IDENTITY) — tokens must name its public key as `sub` to be accepted.
SENDER_KEY = build_packet()[16:48]


def make_token(
    *,
    sub: str | None = None,
    eid: str = EVENT_ID,
    exp: int | None = None,
    key: Ed25519PrivateKey = ORGANISER_KEY,
) -> str:
    claims = {
        "sub": sub if sub is not None else SENDER_KEY.hex(),
        "eid": eid,
        "iat": int(time.time()),
        "exp": exp if exp is not None else int(time.time()) + 3600,
    }
    return jwt.encode(claims, key, algorithm="EdDSA")


def make_cache() -> AttestationCache:
    return AttestationCache(ORGANISER_PUBKEY, EVENT_ID)


class TestAttestationCacheValidation:
    def test_valid_token_accepted(self):
        assert make_cache().add_token(make_token()) == SENDER_KEY

    def test_expired_token_rejected(self):
        with pytest.raises(ValueError, match="expired"):
            make_cache().add_token(make_token(exp=int(time.time()) - 10))

    def test_wrong_event_token_rejected(self):
        with pytest.raises(ValueError, match="wrong event"):
            make_cache().add_token(make_token(eid="other-event-999"))

    def test_forged_token_rejected(self):
        with pytest.raises(ValueError, match="invalid attestation token"):
            make_cache().add_token(make_token(key=Ed25519PrivateKey.generate()))

    def test_garbage_token_rejected(self):
        with pytest.raises(ValueError, match="invalid attestation token"):
            make_cache().add_token("not a jwt at all")


class TestPipelineGating:
    def test_unattested_sender_dropped_at_step_7(self):
        result = RelayPipeline(attestation=make_cache()).process(build_packet())
        assert result.outcome == Outcome.DROP
        assert "attestation failed" in result.drop_reason

    def test_attested_sender_passes(self):
        cache = make_cache()
        cache.add_token(make_token())
        result = RelayPipeline(attestation=cache).process(build_packet())
        assert result.outcome == Outcome.DELIVER

    def test_attestation_expires_with_token(self):
        cache = make_cache()
        cache.add_token(make_token(exp=int(time.time()) + 1))
        time.sleep(1.1)
        result = RelayPipeline(attestation=cache).process(build_packet())
        assert result.outcome == Outcome.DROP
        assert "attestation failed" in result.drop_reason


def make_relay_with_attestation(peers):
    transport = FakeTransport(peers)
    backhaul = RecordingBackhaul()
    cache = make_cache()
    relay = NodeRelay(
        transport=transport, backhaul=backhaul, zone_id=1, attestation=cache,
    )
    return relay, transport, backhaul, cache


def test_node_swallows_presentation_but_spreads_it_over_backhaul():
    _, transport, backhaul, _ = make_relay_with_attestation(["phoneA", "phoneB"])

    presentation = build_packet(
        msg_id=b"\x0d" * 16,
        msg_type=MSG_TYPE_ATTESTATION_PRESENT,
        payload=make_token().encode(),
        zone_id=BROADCAST_ZONE,
    )
    transport.deliver("phoneA", presentation)

    assert transport.sent == []                  # never fanned out to phones
    assert backhaul.broadcast == [presentation]  # other nodes learn it

    # ...and the attested phone's normal message now relays as usual.
    transport.deliver("phoneA", build_packet(msg_id=b"\x0e" * 16, zone_id=1))
    assert [p for p, _ in transport.sent] == ["phoneB"]


def test_unattested_sender_still_refused_relay():
    _, transport, _, _ = make_relay_with_attestation(["phoneA", "phoneB"])
    transport.deliver("phoneA", build_packet(msg_id=b"\x0f" * 16, zone_id=1))
    assert transport.sent == []


def test_bad_presentation_rejected_without_attesting():
    _, transport, backhaul, _ = make_relay_with_attestation(["phoneA", "phoneB"])

    presentation = build_packet(
        msg_id=b"\x10" * 16,
        msg_type=MSG_TYPE_ATTESTATION_PRESENT,
        payload=make_token(eid="other-event-999").encode(),
        zone_id=BROADCAST_ZONE,
    )
    transport.deliver("phoneA", presentation)

    assert backhaul.broadcast == []  # never spread — rejected before caching
    transport.deliver("phoneA", build_packet(msg_id=b"\x11" * 16, zone_id=1))
    assert transport.sent == []  # sender still unattested
