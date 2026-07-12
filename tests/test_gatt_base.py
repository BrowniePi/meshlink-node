"""GattServerBase — the platform-independent half every backend inherits.

These tests exercise reassembly, peer tracking, and outbound framing without
any Bluetooth stack, so they run identically on the Pi and on macOS.
"""
from node import config
from node.ble.base import GattServerBase
from node.ble.framing import frame


class RecordingServer(GattServerBase):
    """Backend stub: records chunks pushed out the TX characteristic."""

    def __init__(self):
        super().__init__()
        self.notified: list[bytes] = []

    def start(self):
        pass

    def run_forever(self):
        pass

    def stop(self):
        pass

    def _notify_chunk(self, data):
        self.notified.append(data)


def test_reassembles_packets_per_peer():
    server = RecordingServer()
    received = []
    server.on_packet = lambda peer, packet: received.append((peer, packet))

    framed_a = frame(b"from-A")
    framed_b = frame(b"from-B")
    # Interleave partial chunks from two peers; streams must not mix.
    server._handle_chunk("peer_A", framed_a[:3])
    server._handle_chunk("peer_B", framed_b[:2])
    server._handle_chunk("peer_A", framed_a[3:])
    server._handle_chunk("peer_B", framed_b[2:])

    assert received == [("peer_A", b"from-A"), ("peer_B", b"from-B")]
    assert server.peers() == ["peer_A", "peer_B"]


def test_corrupt_stream_is_dropped_without_raising():
    server = RecordingServer()
    received = []
    server.on_packet = lambda peer, packet: received.append(packet)

    server._handle_chunk("peer_A", b"\xff\xff")  # length prefix beyond MAX_FRAME
    assert received == []
    # The peer can recover with a fresh, valid frame afterwards.
    server._handle_chunk("peer_A", frame(b"ok"))
    assert received == [b"ok"]


def test_connect_fires_callback_once_per_new_peer():
    server = RecordingServer()
    arrived = []
    server.on_connect = arrived.append

    server._peer_connected("peer_A")
    server._peer_connected("peer_A")  # e.g. a duplicate connect event
    server._peer_connected("peer_B")

    assert arrived == ["peer_A", "peer_B"]


def test_disconnect_clears_peer_state_and_fires_callback():
    server = RecordingServer()
    gone = []
    server.on_disconnect = gone.append

    framed = frame(b"payload")
    server._peer_connected("peer_A")
    server._handle_chunk("peer_A", framed[:3])  # leave a partial buffer behind
    server._peer_disconnected("peer_A")

    assert gone == ["peer_A"]
    assert server.peers() == []
    # Reconnecting must start from a clean buffer, not the stale partial one.
    received = []
    server.on_packet = lambda peer, packet: received.append(packet)
    server._handle_chunk("peer_A", framed)
    assert received == [b"payload"]


def test_send_packet_frames_and_chunks():
    server = RecordingServer()
    packet = bytes(range(256)) * 2  # 512 bytes > two notify chunks when framed

    server.send_packet("peer_A", packet)

    reassembled = b"".join(server.notified)
    assert reassembled == frame(packet)
    assert all(len(c) <= config.BLE_NOTIFY_CHUNK for c in server.notified)
