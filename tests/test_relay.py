from node.backhaul.base import LoggingStubBackhaul, NodeBackhaul
from node.relay import BROADCAST_ZONE, NodeRelay, decrement_ttl
from tests.helpers import FakeTransport, build_packet

ZONE = 1


class RecordingBackhaul(NodeBackhaul):
    def __init__(self):
        self.forwarded: list[tuple[int, bytes]] = []
        self.broadcast: list[bytes] = []

    def forward_to_zone(self, zone_id, packet):
        self.forwarded.append((zone_id, packet))

    def broadcast_to_all_nodes(self, packet):
        self.broadcast.append(packet)


def make_relay(peers):
    transport = FakeTransport(peers)
    backhaul = RecordingBackhaul()
    relay = NodeRelay(transport=transport, backhaul=backhaul, zone_id=ZONE)
    return relay, transport, backhaul


def test_decrement_ttl_touches_only_ttl_byte():
    packet = build_packet(ttl=5, zone_id=ZONE)
    relayed = decrement_ttl(packet)
    assert relayed[68] == 4
    assert relayed[:68] == packet[:68]
    assert relayed[69:] == packet[69:]


def test_accepted_message_relayed_to_other_peers_with_ttl_decremented():
    _, transport, _ = make_relay(["phoneA", "phoneB", "phoneC"])
    packet = build_packet(ttl=5, spray_l=8, zone_id=ZONE)

    transport.deliver("phoneA", packet)

    assert [p for p, _ in transport.sent] == ["phoneB", "phoneC"]
    for _, data in transport.sent:
        # step-8 forward copy: ttl decremented AND spray_L binary-split
        assert data[68] == 4
        assert data[69] == 4
        assert data[:68] == packet[:68]
        assert data[70:] == packet[70:]


def test_dropped_message_not_relayed():
    _, transport, _ = make_relay(["phoneA", "phoneB"])
    transport.deliver("phoneA", build_packet(ttl=0, zone_id=ZONE))  # ttl exhausted
    assert transport.sent == []


def test_duplicate_dropped_by_pipeline_dedup():
    _, transport, _ = make_relay(["phoneA", "phoneB"])
    packet = build_packet(ttl=5, zone_id=ZONE)
    transport.deliver("phoneA", packet)
    transport.deliver("phoneB", decrement_ttl(packet))  # echo of our own relay
    assert [p for p, _ in transport.sent] == ["phoneB"]  # relayed exactly once


def test_own_zone_message_does_not_touch_backhaul():
    _, transport, backhaul = make_relay(["phoneA", "phoneB"])
    transport.deliver("phoneA", build_packet(zone_id=ZONE))
    assert backhaul.forwarded == [] and backhaul.broadcast == []


def test_foreign_zone_message_hits_backhaul_and_still_relays_locally():
    _, transport, backhaul = make_relay(["phoneA", "phoneB"])
    packet = build_packet(zone_id=7)
    transport.deliver("phoneA", packet)
    assert backhaul.forwarded == [(7, packet)]
    assert [p for p, _ in transport.sent] == ["phoneB"]  # single-zone Phase 2


def test_broadcast_zone_hits_backhaul_broadcast():
    _, transport, backhaul = make_relay(["phoneA", "phoneB"])
    packet = build_packet(zone_id=BROADCAST_ZONE)
    transport.deliver("phoneA", packet)
    assert backhaul.broadcast == [packet]
    assert [p for p, _ in transport.sent] == ["phoneB"]


def test_logging_stub_backhaul_is_a_noop(caplog):
    stub = LoggingStubBackhaul()
    with caplog.at_level("INFO", logger="meshlink.backhaul"):
        stub.forward_to_zone(3, b"x" * 10)
        stub.broadcast_to_all_nodes(b"y" * 20)
    assert "no backhaul yet" in caplog.text


def test_stats_count_traffic():
    relay, transport, _ = make_relay(["phoneA", "phoneB"])
    packet = build_packet(ttl=5, zone_id=ZONE)
    transport.deliver("phoneA", packet)                       # accepted + relayed
    transport.deliver("phoneB", decrement_ttl(packet))        # dedup drop
    transport.deliver("phoneA", build_packet(msg_id=b"B" * 16, zone_id=7))
    transport.deliver("phoneA", build_packet(msg_id=b"C" * 16,
                                             zone_id=BROADCAST_ZONE))

    stats = relay.stats()
    assert stats["received"] == 4
    assert stats["accepted"] == 3
    assert stats["dropped"] == 1
    assert stats["relayed_to_phones"] == 3
    assert stats["forwarded_cross_zone"] == 1
    assert stats["broadcast_to_nodes"] == 1


def test_stats_snapshot_is_a_copy():
    relay, _, _ = make_relay([])
    relay.stats()["received"] = 999
    assert relay.stats()["received"] == 0


def test_start_stop_passthrough():
    relay, transport, _ = make_relay([])
    relay.start()
    assert transport.started
    relay.stop()
    assert not transport.started
