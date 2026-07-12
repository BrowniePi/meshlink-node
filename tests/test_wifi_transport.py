"""Phone-facing WiFi transport (Phase 6, task: app-side WiFi transport peer)."""
import socket
import struct
import time

import pytest

from node.ble.framing import frame
from node.transport.multi_transport import MultiTransport
from node.transport.wifi_transport import WifiTransport


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def transport():
    t = WifiTransport("127.0.0.1", 0)
    t.start()
    yield t
    t.stop()


def _connect(transport):
    conn = socket.create_connection(("127.0.0.1", transport.port))
    conn.settimeout(2.0)
    return conn


def _recv_packet(conn):
    header = conn.recv(2)
    (length,) = struct.unpack(">H", header)
    buf = b""
    while len(buf) < length:
        buf += conn.recv(length - len(buf))
    return buf


def test_receive_framed_packet(transport):
    received = []
    transport.on_receive(lambda peer, data: received.append((peer, data)))
    with _connect(transport) as conn:
        conn.sendall(frame(b"hello-mesh"))
        assert _wait_for(lambda: received)
    peer, data = received[0]
    assert peer.startswith("wifi:127.0.0.1:")
    assert data == b"hello-mesh"


def test_send_frames_back_to_peer(transport):
    with _connect(transport) as conn:
        assert _wait_for(lambda: transport.list_peers())
        peer = transport.list_peers()[0]
        transport.send(peer, b"downstream")
        assert _recv_packet(conn) == b"downstream"


def test_disconnect_drops_peer(transport):
    conn = _connect(transport)
    assert _wait_for(lambda: transport.list_peers())
    conn.close()
    assert _wait_for(lambda: not transport.list_peers())


def test_corrupt_length_prefix_drops_connection(transport):
    with _connect(transport) as conn:
        assert _wait_for(lambda: transport.list_peers())
        conn.sendall(struct.pack(">H", 0xFFFF))  # > MAX_FRAME
        assert _wait_for(lambda: not transport.list_peers())


def test_send_to_unknown_peer_is_a_noop(transport):
    transport.send("wifi:10.78.0.99:1234", b"x")  # must not raise


def test_on_connect_fires_for_a_new_phone(transport):
    connected = []
    transport.on_connect(connected.append)
    with _connect(transport):
        assert _wait_for(lambda: connected)
    assert connected[0].startswith("wifi:127.0.0.1:")


def test_unbindable_address_degrades_to_inert():
    t = WifiTransport("192.0.2.1", 7800)  # TEST-NET, not a local interface
    t.start()
    assert not t.active
    assert t.list_peers() == []
    t.stop()


class _FakeBle:
    def __init__(self):
        self.sent = []
        self.callback = None
        self.connect_callback = None

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, peer_id, data):
        self.sent.append((peer_id, data))

    def on_receive(self, callback):
        self.callback = callback

    def on_connect(self, callback):
        self.connect_callback = callback

    def list_peers(self):
        return ["central:abc"]


def test_multi_transport_routes_by_prefix_and_unions_peers(transport):
    ble = _FakeBle()
    multi = MultiTransport(ble, transport)
    received = []
    multi.on_receive(lambda peer, data: received.append(peer))

    with _connect(transport) as conn:
        assert _wait_for(lambda: transport.list_peers())
        wifi_peer = transport.list_peers()[0]
        assert set(multi.list_peers()) == {"central:abc", wifi_peer}

        multi.send("central:abc", b"over-ble")
        assert ble.sent == [("central:abc", b"over-ble")]
        multi.send(wifi_peer, b"over-wifi")
        assert _recv_packet(conn) == b"over-wifi"

        # Receives from either child reach the one relay callback.
        ble.callback("central:abc", b"in-ble")
        conn.sendall(frame(b"in-wifi"))
        assert _wait_for(lambda: len(received) == 2)


def test_multi_transport_forwards_on_connect_to_both_children(transport):
    ble = _FakeBle()
    multi = MultiTransport(ble, transport)
    connected = []
    multi.on_connect(connected.append)

    ble.connect_callback("central:xyz")
    assert connected == ["central:xyz"]

    with _connect(transport):
        assert _wait_for(lambda: len(connected) == 2)
    assert connected[1].startswith("wifi:127.0.0.1:")
