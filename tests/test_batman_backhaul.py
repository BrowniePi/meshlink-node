"""BatmanBackhaul over real UDP sockets (loopback stands in for bat0).

Two backhaul instances on 127.0.0.1 with distinct ports play Node A and
Node B; everything above the socket is the exact production code path.
"""
import threading
import time

import pytest

from node.backhaul.batman_backhaul import BatmanBackhaul
from node.relay import NodeRelay
from tests.helpers import FakeTransport, build_packet

RECV_TIMEOUT = 2.0


class Inbox:
    def __init__(self):
        self.packets: list[tuple[str, bytes]] = []
        self._event = threading.Event()

    def __call__(self, peer_id: str, raw: bytes) -> None:
        self.packets.append((peer_id, raw))
        self._event.set()

    def wait(self) -> bool:
        got = self._event.wait(RECV_TIMEOUT)
        self._event.clear()
        return got


def make_pair():
    """Nodes A (zone 1) and B (zone 2) on loopback, mutually addressed."""
    zone_table: dict[int, tuple[str, int]] = {}
    a = BatmanBackhaul(zone_id=1, zone_table=zone_table,
                       broadcast_addr=("127.0.0.1", 1), bind=("127.0.0.1", 0))
    b = BatmanBackhaul(zone_id=2, zone_table=zone_table,
                       broadcast_addr=("127.0.0.1", 1), bind=("127.0.0.1", 0))
    zone_table[1] = ("127.0.0.1", a.port)
    zone_table[2] = ("127.0.0.1", b.port)
    # Loopback has no subnet broadcast — point "everyone" at the other node.
    a._broadcast_addr = zone_table[2]
    b._broadcast_addr = zone_table[1]
    return a, b


@pytest.fixture
def pair():
    a, b = make_pair()
    yield a, b
    a.stop()
    b.stop()


def test_forward_to_zone_delivers_to_that_nodes_listener(pair):
    a, b = pair
    inbox = Inbox()
    b.on_receive(inbox)
    b.start()

    packet = build_packet(zone_id=2)
    a.forward_to_zone(2, packet)

    assert inbox.wait()
    [(peer_id, raw)] = inbox.packets
    assert raw == packet
    assert peer_id == f"backhaul:127.0.0.1:{a.port}"


def test_broadcast_reaches_other_nodes(pair):
    a, b = pair
    inbox = Inbox()
    b.on_receive(inbox)
    b.start()

    packet = build_packet(zone_id=0xFFFF)
    a.broadcast_to_all_nodes(packet)

    assert inbox.wait()
    assert inbox.packets[0][1] == packet


def test_unknown_zone_falls_back_to_flooding(pair, caplog):
    a, b = pair
    inbox = Inbox()
    b.on_receive(inbox)
    b.start()

    packet = build_packet(zone_id=9)
    with caplog.at_level("WARNING", logger="meshlink.backhaul"):
        a.forward_to_zone(9, packet)  # zone 9 not in the table

    assert inbox.wait()
    assert inbox.packets[0][1] == packet
    assert "no node known for zone 9" in caplog.text


def test_own_zone_forward_is_dropped_with_warning(pair, caplog):
    a, _ = pair
    with caplog.at_level("WARNING", logger="meshlink.backhaul"):
        a.forward_to_zone(1, build_packet(zone_id=1))
    assert "own zone" in caplog.text


def test_unreachable_node_logs_and_drops_without_crashing(pair, caplog, monkeypatch):
    a, _ = pair

    class UnreachableSock:
        def sendto(self, *args):
            raise OSError("Network is unreachable")

        def close(self):
            pass

    monkeypatch.setattr(a, "_sock", UnreachableSock())
    with caplog.at_level("ERROR", logger="meshlink.backhaul"):
        a.forward_to_zone(2, build_packet(zone_id=2))  # must not raise
    assert "packet dropped" in caplog.text


def test_own_broadcast_echo_is_ignored(pair):
    a, _ = pair
    inbox = Inbox()
    a.on_receive(inbox)
    a.start()

    # A datagram whose source is A's own table endpoint = its own broadcast
    # looping back; the listener must skip it.
    a._sock.sendto(build_packet(zone_id=0xFFFF), ("127.0.0.1", a.port))

    assert not inbox.wait()
    assert inbox.packets == []


def test_full_cross_node_chain_phone_to_phone(pair):
    """The Phase 3 money path: Phone A1 → Node A → backhaul → Node B → Phone B1.

    Only the BLE radio is faked — NodeRelay, the meshlink-core pipeline, and
    the backhaul UDP sockets are all production code.
    """
    a, b = pair
    transport_a = FakeTransport(["phoneA1"])
    transport_b = FakeTransport(["phoneB1", "phoneB2"])
    NodeRelay(transport=transport_a, backhaul=a, zone_id=1)
    NodeRelay(transport=transport_b, backhaul=b, zone_id=2)
    a.start()
    b.start()

    packet = build_packet(ttl=5, zone_id=2)  # destined for Node B's zone
    transport_a.deliver("phoneA1", packet)

    deadline = time.monotonic() + RECV_TIMEOUT
    while len(transport_b.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    # Node B relayed the backhaul packet to both of its phones, ttl 5→4.
    assert [p for p, _ in transport_b.sent] == ["phoneB1", "phoneB2"]
    for _, raw in transport_b.sent:
        assert raw[68] == 4
        assert raw[:68] == packet[:68]
