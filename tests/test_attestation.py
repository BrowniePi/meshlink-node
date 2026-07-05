"""Task 5: real attestation validation at pipeline step 7.

Tokens here are real EdDSA compact JWTs signed with a test organiser key —
the same shape meshlink-backend issues — verified by AttestationValidator
through the full vendored-core pipeline.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from node.attestation.token_cache import AttestationValidator, TokenCache
from node.core import (
    MSG_TYPE_ATTESTATION,
    Outcome,
    RelayPipeline,
    set_attestation_validator,
)
from node.relay import BROADCAST_ZONE, NodeRelay
from tests.helpers import FakeTransport, build_packet
from tests.test_relay import RecordingBackhaul

EVENT_ID = "test-event-001"

ORGANISER_KEY = Ed25519PrivateKey.generate()
ORGANISER_PUBKEY_HEX = ORGANISER_KEY.public_key().public_bytes(
    Encoding.Raw, PublicFormat.Raw
).hex()

# The identity every helper-built packet is signed with (vendored core's
# TEST_IDENTITY) — tokens must name its public key as `sub` to be accepted.
SENDER_KEY = build_packet()[16:48]


def make_token(
    *,
    sub: str | None = None,
    eid: str = EVENT_ID,
    exp: int | None = None,
    key: Ed25519PrivateKey = ORGANISER_KEY,
) -> bytes:
    claims = {
        "sub": sub if sub is not None else SENDER_KEY.hex(),
        "eid": eid,
        "iat": int(time.time()),
        "exp": exp if exp is not None else int(time.time()) + 3600,
    }
    return jwt.encode(claims, key, algorithm="EdDSA").encode("ascii")


@pytest.fixture
def pipeline():
    set_attestation_validator(
        AttestationValidator(ORGANISER_PUBKEY_HEX, EVENT_ID)
    )
    yield RelayPipeline()
    set_attestation_validator(None)


def present(pipeline, token: bytes, msg_id=b"\x0a" * 16):
    return pipeline.process(
        build_packet(msg_id=msg_id, msg_type=MSG_TYPE_ATTESTATION, payload=token)
    )


def test_valid_token_attests_sender_and_passes_normal_traffic(pipeline):
    result = present(pipeline, make_token())
    assert result.outcome == Outcome.RELAY  # swallowed, spread to other nodes
    assert result.drop_reason is None

    result = pipeline.process(build_packet(msg_id=b"\x0b" * 16))
    assert result.outcome == Outcome.DELIVER


def test_expired_token_rejected(pipeline):
    result = present(pipeline, make_token(exp=int(time.time()) - 10))
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "attestation token expired"


def test_wrong_event_token_rejected(pipeline):
    result = present(pipeline, make_token(eid="other-event-999"))
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "attestation token for wrong event"


def test_forged_token_rejected(pipeline):
    result = present(pipeline, make_token(key=Ed25519PrivateKey.generate()))
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "attestation token signature invalid"


def test_stolen_token_rejected_on_sub_mismatch(pipeline):
    # A valid token for some other device, presented over a connection whose
    # packets are signed by TEST_IDENTITY: steps 1-6 pass, step 7 must not.
    result = present(pipeline, make_token(sub="ab" * 32))
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "attestation token subject mismatch"


def test_garbage_presentation_rejected(pipeline):
    result = present(pipeline, b"not a jwt at all")
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "attestation token malformed"


def test_unattested_sender_dropped_at_step_7(pipeline):
    result = pipeline.process(build_packet())
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "no valid ticket-bound identity token"


def test_attestation_expires_with_token(pipeline):
    present(pipeline, make_token(exp=int(time.time()) + 1))
    time.sleep(1.1)
    result = pipeline.process(build_packet(msg_id=b"\x0c" * 16))
    assert result.outcome == Outcome.DROP
    assert result.drop_reason == "no valid ticket-bound identity token"


def test_token_cache_evicts_on_exp():
    cache = TokenCache()
    cache.put(b"k" * 32, exp=1000)
    assert cache.is_valid(b"k" * 32, now=999)
    assert not cache.is_valid(b"k" * 32, now=1000)
    assert not cache.is_valid(b"k" * 32, now=999)  # evicted, not just hidden


def test_token_cache_lru_capacity():
    cache = TokenCache(capacity=2)
    for name in (b"a", b"b", b"c"):
        cache.put(name * 32, exp=10_000)
    assert not cache.is_valid(b"a" * 32, now=0)  # oldest evicted
    assert cache.is_valid(b"b" * 32, now=0)
    assert cache.is_valid(b"c" * 32, now=0)


def test_node_swallows_presentation_but_spreads_it_over_backhaul():
    set_attestation_validator(
        AttestationValidator(ORGANISER_PUBKEY_HEX, EVENT_ID)
    )
    try:
        transport = FakeTransport(["phoneA", "phoneB"])
        backhaul = RecordingBackhaul()
        NodeRelay(transport=transport, backhaul=backhaul, zone_id=1)

        presentation = build_packet(
            msg_id=b"\x0d" * 16,
            msg_type=MSG_TYPE_ATTESTATION,
            payload=make_token(),
            zone_id=BROADCAST_ZONE,
        )
        transport.deliver("phoneA", presentation)

        assert transport.sent == []           # never fanned out to phones
        assert backhaul.broadcast == [presentation]  # other nodes learn it

        # ...and the attested phone's normal message now relays as usual.
        transport.deliver("phoneA", build_packet(msg_id=b"\x0e" * 16, zone_id=1))
        assert [p for p, _ in transport.sent] == ["phoneB"]
    finally:
        set_attestation_validator(None)
