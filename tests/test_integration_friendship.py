"""Cross-repo integration: the full friendship/location loop against a REAL
backend (Meshlink-backend running from its sibling checkout) and a real
in-process node.

    create accounts -> node syncs directory -> befriend (mirrored) ->
    share (token) -> beacon -> query -> sealed response -> revoke -> refused

Skipped automatically when the sibling backend checkout/venv is missing.
Run: .venv/bin/python -m pytest tests/test_integration_friendship.py -v
"""
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

import node.core  # noqa: F401 — puts the vendored core on sys.path
from capability.token import issue, parse as parse_token, revocation_key
from crypto.sealed import generate_encryption_keypair
from identity import build_signed_packet, generate_keypair
from location.wire import (
    LocationRevokePayload,
    decode_location_response,
    encode_location_beacon,
    encode_location_query,
    encode_location_revoke,
)
from node.backhaul.base import LoggingStubBackhaul
from node.core import MessageType, parse_packet
from node.directory.cache import DirectoryCache
from node.location.authz import LocationAuthz
from node.location.service import LocationService
from node.location.store import LocationStore
from node.relay import NodeRelay
from tests.helpers import FakeTransport

BACKEND = Path(__file__).resolve().parents[2] / "Meshlink-backend"
BACKEND_PY = BACKEND / ".venv" / "bin" / "python"

pytestmark = pytest.mark.skipif(
    not BACKEND_PY.exists(),
    reason="sibling Meshlink-backend checkout with .venv not found",
)

ZONE = 7

ALICE = generate_keypair()  # shares her location
BOB = generate_keypair()  # her friend, allowed to ask
ALICE_CURVE_PRIV, ALICE_CURVE_PUB = generate_encryption_keypair()
BOB_CURVE_PRIV, BOB_CURVE_PUB = generate_encryption_keypair()


def _post(url: str, body: dict, expect: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == expect, f"{url}: {resp.status}"
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:  # non-2xx still asserts the code
        assert exc.code == expect, f"{url}: {exc.code}"
        return {}


@pytest.fixture(scope="module")
def backend_url(tmp_path_factory):
    """The real backend app served by its own venv on a scratch SQLite DB."""
    tmp = tmp_path_factory.mktemp("backend")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(
        os.environ,
        MESHLINK_DB=str(tmp / "meshlink.db"),
        MESHLINK_DATABASE_URL=str(tmp / "directory.db"),
    )
    proc = subprocess.Popen(
        [str(BACKEND_PY), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/directory/sync", timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError("backend process died on startup")
                time.sleep(0.1)
        else:
            raise RuntimeError("backend never became ready")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture()
def rig(backend_url, tmp_path):
    """A node whose directory cache is synced from the live backend."""
    directory = DirectoryCache(
        backend_url, tmp_path / "directory_cache.json", "test-event-001")
    transport = FakeTransport(peers=["phone-alice", "phone-bob"])
    store = LocationStore()
    # Advanceable clock so the post-revoke query isn't refused by the
    # 60 s rate limit instead of by the revocation set. Starts at real time
    # because token validity windows are checked against it too.
    clock = {"now": time.time()}
    service = LocationService(
        store=store,
        authz=LocationAuthz(store, directory, clock=lambda: clock["now"]),
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
    return relay, transport, store, directory, clock


def _packet(who, msg_type, payload, ephem):
    return build_signed_packet(
        who, ephem_id=ephem, ttl=3, spray_l=1, zone_id=ZONE,
        msg_type=msg_type, payload=payload)


def test_full_friendship_location_loop(backend_url, rig):
    relay, transport, store, directory, clock = rig

    # 1. Both phones create accounts on the real backend.
    for name, curve, ed in [
        ("alice", ALICE_CURVE_PUB, ALICE.public_key),
        ("bob", BOB_CURVE_PUB, BOB.public_key),
    ]:
        _post(f"{backend_url}/account", {
            "username": name,
            "curve25519_pub": curve.hex(),
            "ed25519_pub": ed.hex(),
        }, expect=201)
    # Duplicate usernames are rejected.
    _post(f"{backend_url}/account", {
        "username": "alice",
        "curve25519_pub": BOB_CURVE_PUB.hex(),
        "ed25519_pub": BOB.public_key.hex(),
    }, expect=409)

    # 2. The node pulls the directory from the live backend.
    assert directory.refresh() is True
    assert directory.by_username("alice")["ed25519_pub"] == ALICE.public_key.hex()
    assert directory.user_count() == 2

    # 3. Friendship happens phone-to-phone (mutual consent, off-backend);
    #    the phones mirror the result — backend stores the graph, never a
    #    location.
    _post(f"{backend_url}/friendships", {
        "user_a": "alice", "user_b": "bob", "state": "friends",
        "a_shares_loc": True, "b_shares_loc": False,
    }, expect=200)

    # 4. Alice shares: mints Bob a capability token, and her phone beacons.
    token = issue(ALICE.signing_key, BOB.public_key)
    transport.deliver(
        "phone-alice",
        _packet(ALICE, MessageType.LOCATION,
                encode_location_beacon(51503298, -127144, 5), b"\x0a" * 16))
    assert store.get(ALICE.public_key) is not None
    assert transport.sent == [], "a beacon must never be fanned out"

    # 5. Bob queries through the node and gets a response sealed to HIM.
    transport.deliver(
        "phone-bob",
        _packet(BOB, MessageType.LOCATION_QUERY,
                encode_location_query(token), b"\x0b" * 16))
    assert len(transport.sent) == 1
    peer, raw = transport.sent[0]
    assert peer == "phone-bob"
    msg = parse_packet(raw)
    assert msg.msg_type == MessageType.LOCATION_RESPONSE
    payload = decode_location_response(msg.payload, BOB_CURVE_PRIV)
    assert (payload.lat_microdeg, payload.lon_microdeg) == (51503298, -127144)
    with pytest.raises(Exception):
        decode_location_response(msg.payload, ALICE_CURVE_PRIV)

    # 6. Alice revokes; the same token is now refused, silently.
    transport.sent.clear()
    transport.deliver(
        "phone-alice",
        _packet(ALICE, MessageType.LOCATION_REVOKE,
                encode_location_revoke(
                    LocationRevokePayload(*revocation_key(parse_token(token)))),
                b"\x0a" * 16))
    transport.sent.clear()  # the revoke itself fans out to phones
    clock["now"] += 61  # step past the rate limit — the refusal below must
    # come from the revocation set, not the query spacing
    transport.deliver(
        "phone-bob",
        _packet(BOB, MessageType.LOCATION_QUERY,
                encode_location_query(token), b"\x0b" * 16))
    assert transport.sent == [], "revoked token must be refused silently"

    # 7. The friendship mirror is queryable — and carries no coordinates
    #    (the backend has nowhere to put one; asserted structurally in its
    #    own test_no_location_columns_anywhere).
    with urllib.request.urlopen(f"{backend_url}/friendships/alice") as r:
        friendships = json.loads(r.read())
    assert friendships["count"] == 1
    assert "lat" not in json.dumps(friendships).lower()
