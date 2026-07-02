"""zone_id wire-format usage (Phase 2: single hardcoded zone).

Confirms the zone_id field exists in the shared message format (spec
docs/message-format.md §2, offset 70) and in the actual encode/decode path,
and that the node's hardcoded zone is wired into the relay. Dynamic
multi-zone routing is explicitly out of scope until Phase 7.
"""
from node import config
from node.core import parse_packet
from tests.helpers import build_packet


def test_zone_id_round_trips_through_encode_decode():
    packet = build_packet(zone_id=0x0102)
    assert parse_packet(packet).zone_id == 0x0102
    # offset 70, big-endian uint16 per the spec's packet layout
    assert packet[70:72] == b"\x01\x02"


def test_node_zone_id_is_configured():
    assert 0 < config.NODE_ZONE_ID < 0xFFFF  # 0x0000 reserved, 0xFFFF broadcast


def test_relay_treats_own_zone_as_local():
    from node.relay import NodeRelay
    from tests.helpers import FakeTransport

    transport = FakeTransport(["phoneA", "phoneB"])

    class NoBackhaul:
        def forward_to_zone(self, zone_id, packet):
            raise AssertionError("own-zone message must not hit the backhaul")

        def broadcast_to_all_nodes(self, packet):
            raise AssertionError("own-zone message must not hit the backhaul")

    NodeRelay(transport=transport, backhaul=NoBackhaul(), zone_id=config.NODE_ZONE_ID)
    transport.deliver("phoneA", build_packet(zone_id=config.NODE_ZONE_ID))
    assert [p for p, _ in transport.sent] == ["phoneB"]
