"""Heartbeat sender: periodic POSTs, correct fields, failure isolation."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from node.monitoring.heartbeat_sender import HeartbeatSender
from tests.helpers import FakeTransport
from tests.test_relay import RecordingBackhaul


class RecordingBackend:
    """Minimal local stand-in for POST /heartbeat."""

    def __init__(self):
        self.received: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.received.append(json.loads(body))
                self.send_response(201)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend():
    b = RecordingBackend()
    yield b
    b.close()


class FakeRelay:
    """Stands in for NodeRelay.stats()."""

    def stats(self):
        return {"received": 5, "accepted": 4, "dropped": 1}


def make_sender(url, interval_s=0.05, phones=("phoneA", "wifi:10.78.0.9:1234")):
    backhaul = RecordingBackhaul()
    backhaul.peer_count = lambda: 2
    return HeartbeatSender(
        node_id="test-node",
        zone_id=3,
        zone_name="Main Stage",
        base_url=url,
        transport=FakeTransport(list(phones)),
        backhaul=backhaul,
        relay=FakeRelay(),
        interval_s=interval_s,
        timeout_s=1.0,
    )


def test_periodic_beats_with_expected_fields(backend):
    sender = make_sender(backend.url)
    sender.start()
    deadline = time.time() + 3
    while len(backend.received) < 2 and time.time() < deadline:
        time.sleep(0.02)
    sender.stop()

    assert len(backend.received) >= 2
    beat = backend.received[-1]
    assert beat["heartbeat_version"] == 2
    assert beat["node_id"] == "test-node"
    assert beat["zone_id"] == 3
    assert beat["zone_name"] == "Main Stage"
    assert beat["connected_phone_count"] == 2
    assert beat["batman_peer_count"] == 2
    assert beat["uptime_s"] >= 0
    # uptime advances between beats (same process, monotonic clock)
    assert backend.received[-1]["uptime_s"] >= backend.received[0]["uptime_s"]


def test_v2_payload_blocks(backend):
    sender = make_sender(backend.url)
    sender._started_at = time.monotonic()
    beat = sender.build_payload()

    # phones split by transport, wifi identified by peer-id prefix
    assert beat["phones"] == {
        "ble_count": 1,
        "wifi_count": 1,
        "peers": ["phoneA", "wifi:10.78.0.9:1234"],
    }
    assert beat["relay"] == {"received": 5, "accepted": 4, "dropped": 1}
    # battery is a dict on machines that have one, null otherwise — either
    # way it's present and JSON-serialisable
    assert "battery" in beat
    assert beat["system"]["platform"]
    assert "sent_at" in beat
    json.dumps(beat)


def test_relay_block_null_without_relay(backend):
    sender = make_sender(backend.url)
    sender._relay = None
    sender._started_at = time.monotonic()
    assert sender.build_payload()["relay"] is None


def test_backend_down_never_raises():
    sender = make_sender("http://127.0.0.1:9")  # nothing listens on port 9
    sender._started_at = time.monotonic()
    sender.beat()  # must swallow the connection failure


def test_stop_terminates_thread(backend):
    sender = make_sender(backend.url, interval_s=30)
    sender.start()
    sender.stop()
    assert not sender._thread.is_alive()
