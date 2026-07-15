"""Backend-via-node proxy: wire codec, allowlist, uplink calls, relay demux,
and the channel selection in config."""
import importlib
import io
import json
import sys
import urllib.error

import pytest

from node.backend_proxy import (
    BACKEND_PROXY_MAGIC,
    BackendProxyService,
    decode,
    encode_request,
    encode_response,
    is_backend_proxy_frame,
    path_allowed,
)
from node.relay import NodeRelay
from tests.helpers import FakeTransport
from tests.test_relay import RecordingBackhaul


def make_service(peers=("phoneA",), base_url="http://backend:8000"):
    transport = FakeTransport(list(peers))
    service = BackendProxyService(transport=transport, base_url=base_url)
    return service, transport


def serve_and_reply(service, transport, frame, peer="phoneA"):
    """Run one request synchronously and return the decoded reply."""
    body = decode(frame)
    service._serve(peer, body)  # bypass the pool: deterministic tests
    assert transport.sent, "no reply frame sent"
    peer_id, reply = transport.sent[-1]
    assert peer_id == peer
    return decode(reply)


# -- wire codec ----------------------------------------------------------------

def test_encode_request_matches_wire_contract():
    frame = encode_request("a3", "POST", "/tickets", body='{"x":1}',
                           headers={"authorization": "Bearer t"})
    assert frame == (
        b'MLBP1{"t":"req","id":"a3","method":"POST","path":"/tickets",'
        b'"headers":{"authorization":"Bearer t"},"body":"{\\"x\\":1}"}'
    )


def test_encode_response_success_and_error():
    assert encode_response("a3", 201, body="{}") == \
        b'MLBP1{"t":"res","id":"a3","status":201,"body":"{}"}'
    assert encode_response("a3", 0, error="backend unreachable") == \
        b'MLBP1{"t":"res","id":"a3","status":0,"error":"backend unreachable"}'


def test_is_backend_proxy_frame_matches_only_the_magic():
    assert is_backend_proxy_frame(encode_request("1", "GET", "/events"))
    assert not is_backend_proxy_frame(b'MLPP1{"t":"ping"}')
    assert not is_backend_proxy_frame(b"\x8f" * 32)


def test_decode_rejects_non_json_and_missing_t():
    assert decode(BACKEND_PROXY_MAGIC + b"not json") is None
    assert decode(BACKEND_PROXY_MAGIC + b'{"no_t":1}') is None
    assert decode(b"other") is None


# -- allowlist -----------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/auth/login", "/tickets", "/tickets/tk-1/validity?event_id=e",
    "/attestation/token", "/events", "/account", "/directory/sync",
    "/friendships", "/health", "/rest/v1/directory?username=eq.alice",
    "/rest/v1/friendships", "/rest/v1/relay_messages?order=created_at.asc",
    "/rest/v1/rpc/send_friend_request", "/rest/v1/rpc/get_friend_requests",
    "/rest/v1/rpc/accept_friend_request", "/rest/v1/rpc/decline_friend_request",
    "/rest/v1/rpc/send_message", "/rest/v1/rpc/ack_messages",
    "/rest/v1/rpc/put_location_blobs", "/rest/v1/rpc/get_location",
    "/rest/v1/rpc/mirror_friendship",
])
def test_app_facing_paths_allowed(path):
    assert path_allowed(path)


@pytest.mark.parametrize("path", [
    "/heartbeat", "/admin/sync-from-master", "/dashboard", "/noc", "/api/overview",
    "/rest/v1/profiles", "/rest/v1/rpc/operator_only",
    "auth/login", "/auth/../admin", None, 7,
])
def test_operator_paths_and_malformed_refused(path):
    assert not path_allowed(path)


def test_refused_path_answers_status_0_without_touching_the_uplink(monkeypatch):
    service, transport = make_service()

    def boom(*a, **k):  # any uplink call is a test failure
        raise AssertionError("urlopen must not be called")
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    reply = serve_and_reply(service, transport,
                            encode_request("r1", "POST", "/heartbeat"))
    assert reply == {"t": "res", "id": "r1", "status": 0,
                     "error": "path not allowed"}


def test_disallowed_method_refused():
    service, transport = make_service()
    reply = serve_and_reply(service, transport,
                            encode_request("r1", "DELETE", "/account"))
    assert reply["status"] == 0
    assert "method not allowed" in reply["error"]


# -- uplink calls ---------------------------------------------------------------

class FakeHTTPResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_request_forwarded_and_response_returned(monkeypatch):
    service, transport = make_service(base_url="http://backend:8000/")
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["data"] = request.data
        seen["auth"] = request.get_header("Authorization")
        seen["apikey"] = request.get_header("Apikey")
        seen["prefer"] = request.get_header("Prefer")
        seen["content_type"] = request.get_header("Content-type")
        seen["timeout"] = timeout
        return FakeHTTPResponse(b'{"ticket_id":"tk"}', status=201)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    frame = encode_request("r7", "POST", "/tickets", body='{"event_id":"e"}',
                           headers={"authorization": "Bearer tok",
                                    "apikey": "anon", "prefer": "return=minimal",
                                    "cookie": "must-not-cross"})
    reply = serve_and_reply(service, transport, frame)

    assert seen["url"] == "http://backend:8000/tickets"
    assert seen["method"] == "POST"
    assert seen["data"] == b'{"event_id":"e"}'
    assert seen["auth"] == "Bearer tok"
    assert seen["apikey"] == "anon"
    assert seen["prefer"] == "return=minimal"
    assert seen["content_type"] == "application/json"
    assert reply == {"t": "res", "id": "r7", "status": 201,
                     "body": '{"ticket_id":"tk"}'}


def test_backend_http_error_keeps_its_status(monkeypatch):
    service, transport = make_service()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", None,
            io.BytesIO(b'{"detail":"ticket expired"}'))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    reply = serve_and_reply(service, transport,
                            encode_request("r1", "POST", "/attestation/token"))
    assert reply["status"] == 403
    assert json.loads(reply["body"]) == {"detail": "ticket expired"}


def test_unreachable_backend_answers_status_0(monkeypatch):
    service, transport = make_service()

    def fake_urlopen(request, timeout):
        raise OSError("no route to host")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    reply = serve_and_reply(service, transport,
                            encode_request("r1", "GET", "/events"))
    assert reply == {"t": "res", "id": "r1", "status": 0,
                     "error": "backend unreachable"}


# -- relay demux ----------------------------------------------------------------

class RecordingProxy:
    def __init__(self):
        self.frames: list[tuple[str, bytes]] = []

    def handle_frame(self, peer_id, frame):
        self.frames.append((peer_id, frame))


def test_relay_demuxes_proxy_frames_off_the_pipeline():
    transport = FakeTransport(["phoneA", "phoneB"])
    proxy = RecordingProxy()
    relay = NodeRelay(transport=transport, backhaul=RecordingBackhaul(),
                      zone_id=1, backend_proxy=proxy)

    frame = encode_request("r1", "GET", "/events")
    transport.deliver("phoneA", frame)

    assert proxy.frames == [("phoneA", frame)]
    assert transport.sent == []  # never relayed to other phones
    assert relay.stats()["received"] == 0  # not counted as mesh traffic


def test_relay_drops_proxy_frames_when_no_service_wired():
    transport = FakeTransport(["phoneA", "phoneB"])
    NodeRelay(transport=transport, backhaul=RecordingBackhaul(), zone_id=1)

    transport.deliver("phoneA", encode_request("r1", "GET", "/events"))
    assert transport.sent == []


# -- channel selection (config) ---------------------------------------------------

def reload_config(monkeypatch, platform, env=None):
    for key in ("MESHLINK_BACKEND_CHANNEL", "MESHLINK_BACKEND_BATMAN_URL",
                "MESHLINK_BACKEND_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "platform", platform)
    from node import config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    from node import config
    importlib.reload(config)


def test_macos_auto_channel_is_wifi_lan(monkeypatch):
    config = reload_config(monkeypatch, "darwin",
                           {"MESHLINK_BACKEND_URL": "http://192.168.1.20:8000"})
    assert config.BACKEND_CHANNEL == "wifi_lan"
    assert config.BACKEND_URL == "http://192.168.1.20:8000"


def test_pi_auto_channel_is_batman(monkeypatch):
    config = reload_config(monkeypatch, "linux")
    assert config.BACKEND_CHANNEL == "batman"
    assert config.BACKEND_URL == "http://10.77.0.254:8000"


def test_batman_url_override(monkeypatch):
    config = reload_config(
        monkeypatch, "linux",
        {"MESHLINK_BACKEND_BATMAN_URL": "http://10.77.0.9:8000"})
    assert config.BACKEND_URL == "http://10.77.0.9:8000"


def test_channel_can_be_forced_off_platform(monkeypatch):
    config = reload_config(monkeypatch, "linux",
                           {"MESHLINK_BACKEND_CHANNEL": "wifi_lan",
                            "MESHLINK_BACKEND_URL": "http://lan:8000"})
    assert config.BACKEND_CHANNEL == "wifi_lan"
    assert config.BACKEND_URL == "http://lan:8000"
