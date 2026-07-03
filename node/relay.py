"""Node relay: transport → meshlink-core pipeline → connected peers.

The node is never a message destination — anything the pipeline accepts is
relayed to the node's other connected phones, and cross-node cases are handed
to the NodeBackhaul interface (a logging stub until Phase 3).
"""
import logging

from node.backhaul.base import NodeBackhaul
from node.core import Outcome, RelayPipeline, Transport

log = logging.getLogger("meshlink.relay")

BROADCAST_ZONE = 0xFFFF

# ttl byte offset in the fixed header (docs/message-format.md §2).
_TTL_OFFSET = 68


def decrement_ttl(raw: bytes) -> bytes:
    """Return the packet with ttl reduced by one hop.

    The signature covers the ttl byte, so this technically invalidates it —
    signature verification is a stub until Phase 4, which must resolve that
    spec-level tension (mutable ttl inside the signed region) when it lands.
    """
    ttl = raw[_TTL_OFFSET]
    return raw[:_TTL_OFFSET] + bytes([ttl - 1]) + raw[_TTL_OFFSET + 1:]


class NodeRelay:
    def __init__(self, transport: Transport, backhaul: NodeBackhaul, zone_id: int):
        self._transport = transport
        self._backhaul = backhaul
        self._zone_id = zone_id
        self._pipeline = RelayPipeline()
        transport.on_receive(self._handle_packet)
        # Packets arriving from other nodes run the same pipeline as BLE
        # traffic (dedup is what stops cross-node loops). No-op on the stub.
        backhaul.on_receive(self._handle_packet)

    def start(self) -> None:
        self._transport.start()

    def stop(self) -> None:
        self._transport.stop()

    def _handle_packet(self, peer_id: str, raw: bytes) -> None:
        result = self._pipeline.process(raw)
        if result.outcome == Outcome.DROP:
            log.info("drop from %s: %s", peer_id, result.drop_reason)
            return

        msg = result.message

        # Cross-node cases go through the backhaul interface. With one node
        # it's a logging stub — but routing already speaks in these terms so
        # Phase 3 only has to supply a real implementation.
        if msg.zone_id == BROADCAST_ZONE:
            self._backhaul.broadcast_to_all_nodes(raw)
        elif msg.zone_id != self._zone_id:
            self._backhaul.forward_to_zone(msg.zone_id, raw)

        # Phase 2: single node, single zone — every accepted message is also
        # relayed to the local BLE cell regardless of its zone_id.
        relayed = decrement_ttl(raw)
        for peer in self._transport.list_peers():
            if peer == peer_id:
                continue
            self._transport.send(peer, relayed)
        log.info(
            "relayed msg %s from %s (zone %d, ttl %d→%d)",
            msg.msg_id.hex()[:8], peer_id, msg.zone_id, msg.ttl, msg.ttl - 1,
        )
