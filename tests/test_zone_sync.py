"""ZoneSync: announcement codec, learn/ignore logic, and backhaul demux."""
import threading
import time

import pytest

from node.backhaul.batman_backhaul import BatmanBackhaul
from node.backhaul.base import NodeBackhaul
from node.backhaul.dynamic_zone_table import DynamicZoneTable
from node.backhaul.zone_sync import (
    ZONE_SYNC_MAGIC,
    ZoneSync,
    decode_announcement,
    encode_announcement,
)
from tests.helpers import build_packet

RECV_TIMEOUT = 2.0


class FakeBackhaul(NodeBackhaul):
    """Captures control broadcasts and lets a test inject inbound frames."""

    def __init__(self):
        self.control_broadcasts: list[bytes] = []
        self._control_cb = None

    def forward_to_zone(self, zone_id, packet):
        pass

    def broadcast_to_all_nodes(self, packet):
        pass

    def broadcast_control(self, frame):
        self.control_broadcasts.append(frame)

    def on_control(self, callback):
        self._control_cb = callback

    def inject(self, peer_id, frame):
        self._control_cb(peer_id, frame)


# -- codec -----------------------------------------------------------------

def test_encode_decode_round_trip_with_ported_addr():
    frame = encode_announcement("nodeA", 2, ("127.0.0.1", 19790))
    assert frame.startswith(ZONE_SYNC_MAGIC)
    assert decode_announcement(frame) == {
        "node_id": "nodeA", "zone_id": 2, "addr": ("127.0.0.1", 19790),
    }


def test_encode_decode_round_trip_with_bare_host():
    frame = encode_announcement("nodeB", 3, "10.77.0.3")
    assert decode_announcement(frame) == {
        "node_id": "nodeB", "zone_id": 3, "addr": "10.77.0.3",
    }


def test_decode_rejects_non_magic_frame():
    assert decode_announcement(build_packet(zone_id=1)) is None


def test_decode_rejects_malformed_json():
    assert decode_announcement(ZONE_SYNC_MAGIC + b"not json") is None


def test_decode_rejects_missing_fields():
    assert decode_announcement(ZONE_SYNC_MAGIC + b'{"node_id": "x"}') is None


# -- learn / ignore --------------------------------------------------------

def make_sync(backhaul, table, node_id="me", zone_id=1, own_addr="10.77.0.1"):
    return ZoneSync(backhaul, table, node_id=node_id, zone_id=zone_id,
                    own_addr=own_addr, interval_s=999)


def test_on_control_learns_peer_announcement():
    table = DynamicZoneTable(own_zone_id=1)
    backhaul = FakeBackhaul()
    make_sync(backhaul, table)

    backhaul.inject("backhaul:10.77.0.2:19788",
                    encode_announcement("nodeB", 2, "10.77.0.2"))
    assert table.addr_for(2) == "10.77.0.2"


def test_on_control_ignores_own_echo():
    table = DynamicZoneTable(own_zone_id=1)
    backhaul = FakeBackhaul()
    make_sync(backhaul, table, node_id="me", zone_id=1, own_addr="10.77.0.1")

    # Our own broadcast looping back must not create a self-entry.
    backhaul.inject("backhaul:10.77.0.1:19788",
                    encode_announcement("me", 1, "10.77.0.1"))
    assert table.known_zones() == set()


def test_announce_now_broadcasts_own_assignment():
    table = DynamicZoneTable(own_zone_id=4)
    backhaul = FakeBackhaul()
    sync = make_sync(backhaul, table, node_id="node4", zone_id=4,
                     own_addr=("127.0.0.1", 5000))

    sync.announce_now()
    [frame] = backhaul.control_broadcasts
    assert decode_announcement(frame) == {
        "node_id": "node4", "zone_id": 4, "addr": ("127.0.0.1", 5000),
    }


# -- demux over the real backhaul -----------------------------------------

class Inbox:
    def __init__(self):
        self.items: list[tuple[str, bytes]] = []
        self._event = threading.Event()

    def __call__(self, peer_id, raw):
        self.items.append((peer_id, raw))
        self._event.set()

    def wait(self):
        got = self._event.wait(RECV_TIMEOUT)
        self._event.clear()
        return got


def test_control_frames_demux_away_from_the_relay_path():
    """An announcement must reach on_control(), never the relay's on_receive."""
    sender = BatmanBackhaul(zone_id=1, broadcast_addr=("127.0.0.1", 1),
                            bind=("127.0.0.1", 0))
    receiver = BatmanBackhaul(zone_id=2, broadcast_addr=("127.0.0.1", 1),
                              bind=("127.0.0.1", 0))
    sender._broadcast_addr = ("127.0.0.1", receiver.port)
    data_inbox, control_inbox = Inbox(), Inbox()
    receiver.on_receive(data_inbox)
    receiver.on_control(control_inbox)
    receiver.start()
    try:
        sender.broadcast_control(
            encode_announcement("nodeA", 1, ("127.0.0.1", sender.port))
        )
        assert control_inbox.wait()
        assert decode_announcement(control_inbox.items[0][1])["zone_id"] == 1
        assert data_inbox.items == []  # relay never saw the control frame
    finally:
        sender.stop()
        receiver.stop()


def test_fourth_node_becomes_routable_after_it_announces():
    """DoD: a new node joins and zone routing adapts with no config redeploy."""
    existing = BatmanBackhaul(zone_id=1, broadcast_addr=("127.0.0.1", 1),
                              bind=("127.0.0.1", 0))
    existing._own_addr = ("127.0.0.1", existing.port)
    table = DynamicZoneTable(own_zone_id=1)
    existing._table = table
    ZoneSync(existing, table, node_id="node1", zone_id=1,
             own_addr=("127.0.0.1", existing.port), interval_s=999)
    existing.start()

    newcomer = BatmanBackhaul(zone_id=4, broadcast_addr=("127.0.0.1", existing.port),
                              bind=("127.0.0.1", 0))
    newcomer_sync = ZoneSync(newcomer, DynamicZoneTable(own_zone_id=4),
                             node_id="node4", zone_id=4,
                             own_addr=("127.0.0.1", newcomer.port), interval_s=999)
    try:
        assert existing._table.addr_for(4) is None  # unknown before it speaks
        newcomer_sync.announce_now()                # the 4th node joins

        deadline = time.monotonic() + RECV_TIMEOUT
        while existing._table.addr_for(4) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert existing._table.addr_for(4) == ("127.0.0.1", newcomer.port)
    finally:
        newcomer_sync.stop()
        existing.stop()
