"""End-to-end through NodeRelay: location traffic passes the full 8-step
pipeline like any signed message, then gets node-terminated — beacons and
queries are never fanned out to other phones."""
import json

import pytest

import node.core  # noqa: F401 — puts the vendored core on sys.path
from capability.token import issue
from crypto.sealed import generate_encryption_keypair
from identity import build_signed_packet, generate_keypair
from location.wire import (
    decode_location_response,
    encode_location_beacon,
    encode_location_query,
)
from node.backhaul.base import LoggingStubBackhaul
from node.core import MessageType, parse_packet
from node.directory.cache import DirectoryCache
from node.location.authz import LocationAuthz
from node.location.service import LocationService
from node.location.store import LocationStore
from node.relay import NodeRelay
from tests.helpers import FakeTransport

ZONE = 3

TARGET = generate_keypair()
REQUESTER = generate_keypair()
REQ_CURVE_PRIV, REQ_CURVE_PUB = generate_encryption_keypair()


@pytest.fixture()
def rig(tmp_path):
    users = [
        {"username": "target", "curve25519_pub": "aa" * 32,
         "ed25519_pub": TARGET.public_key.hex()},
        {"username": "requester", "curve25519_pub": REQ_CURVE_PUB.hex(),
         "ed25519_pub": REQUESTER.public_key.hex()},
    ]
    cache_path = tmp_path / "directory_cache.json"
    cache_path.write_text(json.dumps(users))
    directory = DirectoryCache("http://127.0.0.1:1", cache_path, "test-event-001")

    transport = FakeTransport(peers=["phone-target", "phone-requester", "phone-other"])
    store = LocationStore()
    service = LocationService(
        store=store,
        authz=LocationAuthz(store, directory),
        transport=transport,
        node_identity=generate_keypair(),
        zone_id=ZONE,
    )
    relay = NodeRelay(
        transport=transport,
        backhaul=LoggingStubBackhaul(),
        zone_id=ZONE,
        location=service,
    )
    return relay, transport, store


def beacon_packet():
    return build_signed_packet(
        TARGET, ephem_id=b"\x01" * 16, ttl=3, spray_l=1, zone_id=ZONE,
        msg_type=MessageType.LOCATION,
        payload=encode_location_beacon(51503298, -127144, 5),
    )


def query_packet(token: bytes):
    return build_signed_packet(
        REQUESTER, ephem_id=b"\x02" * 16, ttl=3, spray_l=1, zone_id=ZONE,
        msg_type=MessageType.LOCATION_QUERY,
        payload=encode_location_query(token),
    )


def test_beacon_feeds_store_and_is_not_fanned_out(rig):
    relay, transport, store = rig
    transport.deliver("phone-target", beacon_packet())
    row = store.get(TARGET.public_key)
    assert row is not None and row.lat_microdeg == 51503298
    # a raw coordinate must never reach other phones
    assert transport.sent == []
    assert relay.stats()["location_terminated"] == 1


def test_happy_path_query_served_encrypted_to_requester(rig):
    relay, transport, store = rig
    transport.deliver("phone-target", beacon_packet())
    token = issue(TARGET.signing_key, REQUESTER.public_key)

    transport.deliver("phone-requester", query_packet(token))

    # exactly one send: the response, straight back to the requester's session
    assert len(transport.sent) == 1
    peer, raw = transport.sent[0]
    assert peer == "phone-requester"
    msg = parse_packet(raw)
    assert msg.msg_type == MessageType.LOCATION_RESPONSE
    payload = decode_location_response(msg.payload, REQ_CURVE_PRIV)
    assert (payload.lat_microdeg, payload.lon_microdeg) == (51503298, -127144)
    # the query itself was never relayed to the target or anyone else
    assert not any(p in ("phone-target", "phone-other") for p, _ in transport.sent)


def test_query_without_valid_token_gets_nothing(rig):
    relay, transport, store = rig
    transport.deliver("phone-target", beacon_packet())
    stranger = generate_keypair()
    forged = issue(stranger.signing_key, REQUESTER.public_key)

    transport.deliver("phone-requester", query_packet(forged))

    assert transport.sent == []  # silent, uniform refusal


def test_direct_message_relays_opaque_and_is_not_node_terminated(rig):
    """DIRECT_MESSAGE (0x0D) is ordinary relay traffic: fanned out like TEXT,
    never consumed by the location service — and the node carries it opaque
    (the text is sealed to the recipient; only the plaintext hint is visible)."""
    from friends.wire import encode_direct_message

    relay, transport, store = rig
    dm = build_signed_packet(
        TARGET, ephem_id=b"\x01" * 16, ttl=3, spray_l=1, zone_id=ZONE,
        msg_type=MessageType.DIRECT_MESSAGE,
        payload=encode_direct_message("meet at gate B", b"\x11" * 8, REQ_CURVE_PUB),
    )
    transport.deliver("phone-target", dm)

    fanned_to = {p for p, _ in transport.sent}
    assert fanned_to == {"phone-requester", "phone-other"}
    assert relay.stats()["location_terminated"] == 0
    assert all(b"meet at gate B" not in raw for _, raw in transport.sent)


def test_revoke_feeds_set_and_still_relays_to_phones(rig):
    relay, transport, store = rig
    transport.deliver("phone-target", beacon_packet())
    token = issue(TARGET.signing_key, REQUESTER.public_key)

    from capability.token import parse as parse_token, revocation_key
    from location.wire import LocationRevokePayload, encode_location_revoke

    revoke = build_signed_packet(
        TARGET, ephem_id=b"\x01" * 16, ttl=3, spray_l=1, zone_id=ZONE,
        msg_type=MessageType.LOCATION_REVOKE,
        payload=encode_location_revoke(
            LocationRevokePayload(*revocation_key(parse_token(token)))),
    )
    transport.deliver("phone-target", revoke)

    # REVOKE also goes target → friend: normal fan-out to the other phones
    fanned_to = {p for p, _ in transport.sent}
    assert fanned_to == {"phone-requester", "phone-other"}

    # and future serving is refused
    transport.sent.clear()
    transport.deliver("phone-requester", query_packet(token))
    assert transport.sent == []
