"""Capability enforcement: only a valid, unrevoked, unexpired token signed by
the target unlocks a coordinate — and every refusal is observably identical."""
import json

import pytest

import node.core  # noqa: F401 — puts the vendored core on sys.path
from capability.token import issue
from crypto.sealed import generate_encryption_keypair
from identity import build_signed_packet, generate_keypair
from location.wire import (
    decode_location_response,
    encode_location_query,
)
from node.core import MessageType, parse_packet
from node.directory.cache import DirectoryCache
from node.location.authz import LocationAuthz
from node.location.store import LocationStore

NOW = 1_800_000_000

TARGET = generate_keypair()      # whose location is being shared
REQUESTER = generate_keypair()   # the friend querying it
STRANGER = generate_keypair()
REQ_CURVE_PRIV, REQ_CURVE_PUB = generate_encryption_keypair()


def make_directory(tmp_path) -> DirectoryCache:
    users = [
        {"username": "target", "curve25519_pub": "aa" * 32,
         "ed25519_pub": TARGET.public_key.hex()},
        {"username": "requester", "curve25519_pub": REQ_CURVE_PUB.hex(),
         "ed25519_pub": REQUESTER.public_key.hex()},
    ]
    cache = tmp_path / "directory_cache.json"
    cache.write_text(json.dumps(users))
    # base_url is never contacted: the cache file is the offline copy
    return DirectoryCache("http://127.0.0.1:1", cache, "test-event-001")


def query_msg(token: bytes, sender=REQUESTER, timestamp=NOW):
    packet = build_signed_packet(
        sender, ephem_id=b"\x05" * 16, ttl=3, spray_l=1, zone_id=3,
        msg_type=MessageType.LOCATION_QUERY,
        payload=encode_location_query(token),
        timestamp=timestamp,
    )
    return parse_packet(packet)


@pytest.fixture()
def setup(tmp_path):
    clock = {"now": float(NOW)}
    store = LocationStore(clock=lambda: clock["now"])
    store.update(TARGET.public_key, lat_microdeg=51503298, lon_microdeg=-127144,
                 accuracy_m=5, zone_id=3)
    authz = LocationAuthz(store, make_directory(tmp_path),
                          clock=lambda: clock["now"])
    return store, authz, clock


def grant(issued_at=NOW - 60, **kwargs):
    return issue(TARGET.signing_key, REQUESTER.public_key,
                 issued_at=issued_at, **kwargs)


def test_valid_token_fresh_beacon_served_encrypted(setup):
    store, authz, clock = setup
    clock["now"] = NOW + 40.0
    response = authz.handle_query(query_msg(grant()))
    assert response is not None
    payload = decode_location_response(response, REQ_CURVE_PRIV)
    assert payload.lat_microdeg == 51503298
    assert payload.lon_microdeg == -127144
    assert payload.accuracy_m == 5
    assert payload.beacon_age_s == 40
    assert payload.zone_id == 3
    # sealed to the requester: a different key cannot open it (invariant 4)
    other_priv, _ = generate_encryption_keypair()
    with pytest.raises(ValueError):
        decode_location_response(response, other_priv)


def test_all_refusals_are_observably_identical(setup):
    """Expired, wrong grantee, revoked, unknown target, no beacon, malformed —
    all produce exactly the same observable outcome (None; nothing sent), so
    a prober cannot tell 'denied' from 'no such user' (§8.3)."""
    store, authz, clock = setup

    refusals = {
        "expired": authz.handle_query(
            query_msg(grant(issued_at=NOW - 7200, expiry_s=3600))),
        "wrong-grantee": authz.handle_query(query_msg(
            issue(TARGET.signing_key, STRANGER.public_key, issued_at=NOW - 60),
            sender=STRANGER)),
        "stranger-forged-issuer": authz.handle_query(query_msg(
            issue(STRANGER.signing_key, REQUESTER.public_key, issued_at=NOW - 60))),
        "malformed-token": authz.handle_query(query_msg(b"\x00" * 98)),
    }
    store.forget(TARGET.public_key)
    refusals["no-beacon"] = authz.handle_query(query_msg(grant()))
    assert all(r is None for r in refusals.values()), refusals


def test_stolen_token_useless_without_grantee_key(setup):
    """The envelope sender must BE the grantee: a stranger replaying a
    legitimately issued token is refused at the signature-verified sender
    check."""
    _, authz, _ = setup
    token = grant()  # valid grant to REQUESTER
    assert authz.handle_query(query_msg(token, sender=STRANGER)) is None


def test_node_cannot_answer_without_token_even_for_real_friends(setup):
    """Invariant 1 from the enforcement side: the directory says these two
    are perfectly real users (a friendship could exist), the beacon is fresh
    — but without a target-signed token there is no code path to the
    coordinate. The node never authors consent."""
    _, authz, _ = setup
    # No token at all (payload is not even token-sized)
    packet = build_signed_packet(
        REQUESTER, ephem_id=b"\x05" * 16, ttl=3, spray_l=1, zone_id=3,
        msg_type=MessageType.LOCATION_QUERY, payload=b"", timestamp=NOW,
    )
    assert authz.handle_query(parse_packet(packet)) is None


def test_revocation(setup):
    _, authz, clock = setup
    token = grant()

    from location.wire import LocationRevokePayload, encode_location_revoke
    from capability.token import parse as parse_token, revocation_key

    rk = revocation_key(parse_token(token))
    revoke_payload = encode_location_revoke(LocationRevokePayload(*rk))

    # revoke from a non-issuer is ignored
    bogus = build_signed_packet(
        STRANGER, ephem_id=b"\x06" * 16, ttl=3, spray_l=1, zone_id=3,
        msg_type=MessageType.LOCATION_REVOKE, payload=revoke_payload,
        timestamp=NOW,
    )
    assert authz.handle_revoke(parse_packet(bogus)) is False
    assert authz.handle_query(query_msg(token)) is not None

    # revoke from the issuer invalidates future serving
    clock["now"] = NOW + 120.0  # move past the query rate-limit window
    real = build_signed_packet(
        TARGET, ephem_id=b"\x07" * 16, ttl=3, spray_l=1, zone_id=3,
        msg_type=MessageType.LOCATION_REVOKE, payload=revoke_payload,
        timestamp=NOW,
    )
    assert authz.handle_revoke(parse_packet(real)) is True
    assert authz.handle_query(query_msg(token)) is None


def test_query_rate_limit_trips_on_rapid_polling(setup):
    """1 query per target per 60 s per requester: a valid token cannot be
    polled into a movement trail (query-side retention invariant)."""
    _, authz, clock = setup
    token = grant()
    assert authz.handle_query(query_msg(token)) is not None
    clock["now"] = NOW + 10.0
    assert authz.handle_query(query_msg(token)) is None   # tripped
    clock["now"] = NOW + 30.0
    assert authz.handle_query(query_msg(token)) is None   # still inside window
    clock["now"] = NOW + 61.0
    assert authz.handle_query(query_msg(token)) is not None
