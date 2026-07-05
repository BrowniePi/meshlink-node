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


def _describe_payload(payload: bytes) -> str:
    """Best-effort human-readable payload for logging.

    Payloads are plaintext until Phase 4 adds encryption, so UTF-8 text is
    the common case; anything else falls back to a hex preview.
    """
    try:
        return repr(payload.decode("utf-8"))
    except UnicodeDecodeError:
        return f"<{len(payload)} bytes: {payload[:32].hex()}{'…' if len(payload) > 32 else ''}>"


def decrement_ttl(raw: bytes) -> bytes:
    """Return the packet with ttl reduced by one hop.

    Safe against the Phase 4 Ed25519 check: the signed region deliberately
    excludes the hop-mutable ttl and spray_L bytes (offsets 68-69, see
    meshlink-core pipeline/message.py:signed_region and DECISIONS.md), so a
    relay decrementing ttl here does not invalidate the sender's signature.
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
        log.info("received %d-byte packet from %s", len(raw), peer_id)
        result = self._pipeline.process(raw)
        if result.outcome == Outcome.DROP:
            log.info("drop from %s: %s", peer_id, result.drop_reason)
            return

        msg = result.message
        log.info(
            "accepted msg %s from %s: %s",
            msg.msg_id.hex()[:8], peer_id, _describe_payload(msg.payload),
        )

        # Cross-node cases go through the backhaul interface. With one node
        # it's a logging stub — but routing already speaks in these terms so
        # Phase 3 only has to supply a real implementation.
        if msg.zone_id == BROADCAST_ZONE:
            self._backhaul.broadcast_to_all_nodes(raw)
        elif msg.zone_id != self._zone_id:
            self._backhaul.forward_to_zone(msg.zone_id, raw)

        # A verified attestation presentation (Outcome.RELAY) is swallowed
        # here: the sender is now in this node's validated cache, and the
        # backhaul forward above lets other nodes learn it too — but phones
        # never see it (a JWT blob means nothing to the app layer).
        if result.outcome == Outcome.RELAY:
            log.info(
                "attestation presentation from %s accepted — sender %s cached, "
                "not delivered to phones",
                peer_id, msg.sender_key.hex()[:16],
            )
            return

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
