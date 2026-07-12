"""BleTransport against a fake GattServer, including the full-pipeline path.

The end-to-end test here is the software half of the Phase 2 demo: two peers
that can only reach the node exchange a message through the unchanged
meshlink-core relay pipeline over the BLE transport abstraction.
"""
import inspect

from node.backhaul.base import LoggingStubBackhaul
from node.core import Transport, parse_packet
from node.relay import NodeRelay
from node.transport.ble_transport import BleTransport
from tests.helpers import build_packet


class FakeGattServer:
    """Same surface as node.ble.base.GattServerBase, no Bluetooth stack."""

    def __init__(self):
        self.on_packet = None
        self.on_connect = None
        self.on_disconnect = None
        self.running = False
        self.sent: list[tuple[str, bytes]] = []
        self._peers: list[str] = []
        self.fail_sends_to: set[str] = set()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def send_packet(self, peer_id, packet):
        if peer_id in self.fail_sends_to:
            raise RuntimeError("peer went away")
        self.sent.append((peer_id, packet))

    def peers(self):
        return list(self._peers)

    def connect(self, peer_id):
        self._peers.append(peer_id)
        if self.on_connect is not None:
            self.on_connect(peer_id)

    def receive(self, peer_id, packet):
        self.on_packet(peer_id, packet)


def test_implements_every_transport_method():
    abstract = {
        name for name, _ in inspect.getmembers(Transport, inspect.isfunction)
        if getattr(getattr(Transport, name), "__isabstractmethod__", False)
    }
    assert abstract == {"start", "stop", "send", "on_receive", "list_peers"}
    assert BleTransport.__abstractmethods__ == frozenset()
    assert issubclass(BleTransport, Transport)


def test_start_stop_and_peer_listing():
    server = FakeGattServer()
    transport = BleTransport(server)
    transport.start()
    assert server.running
    server.connect("dev_AA")
    server.connect("dev_BB")
    assert transport.list_peers() == ["dev_AA", "dev_BB"]
    transport.stop()
    assert not server.running


def test_on_connect_callback_fires_per_new_central():
    server = FakeGattServer()
    transport = BleTransport(server)
    connected = []
    transport.on_connect(connected.append)
    server.connect("dev_AA")
    server.connect("dev_BB")
    assert connected == ["dev_AA", "dev_BB"]


def test_send_failure_is_contained():
    server = FakeGattServer()
    server.fail_sends_to.add("dev_AA")
    transport = BleTransport(server)
    transport.send("dev_AA", b"data")  # must not raise


def test_malformed_inbound_data_is_contained():
    server = FakeGattServer()
    transport = BleTransport(server)
    received = []
    transport.on_receive(lambda p, d: received.append((p, d)))
    server.receive("dev_AA", b"")  # empty reassembly artifact
    server.receive("dev_AA", b"\x00" * 500)  # beyond MAX_PACKET
    assert received == []


def test_receive_callback_exception_is_contained():
    server = FakeGattServer()
    transport = BleTransport(server)

    def bad_callback(peer, data):
        raise ValueError("boom")

    transport.on_receive(bad_callback)
    server.receive("dev_AA", b"\x01" * 10)  # must not raise into the main loop


def test_full_pipeline_round_trip_through_node():
    """Phone A → node (pipeline) → Phone B, over the Transport abstraction,
    with zero changes to relay or pipeline code."""
    server = FakeGattServer()
    transport = BleTransport(server)
    NodeRelay(transport=transport, backhaul=LoggingStubBackhaul(), zone_id=1)

    server.connect("dev_phoneA")
    server.connect("dev_phoneB")

    packet = build_packet(ttl=5, zone_id=1, payload=b"Meet at south gate")
    server.receive("dev_phoneA", packet)

    assert [p for p, _ in server.sent] == ["dev_phoneB"]
    relayed = parse_packet(server.sent[0][1])
    assert relayed.payload == b"Meet at south gate"
    assert relayed.ttl == 4  # one hop consumed at the node
