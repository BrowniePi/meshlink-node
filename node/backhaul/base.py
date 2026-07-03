"""Node-to-node backhaul interface.

Stubbed in Phase 2 (one node, nothing to mesh); implemented for real by
Phase 3's batman_backhaul.py. The node's routing logic speaks only in terms
of this interface — swapping the stub for the batman-adv implementation is
new code, not a rewrite of how the node routes.
"""
import logging
from abc import ABC, abstractmethod
from typing import Callable

log = logging.getLogger("meshlink.backhaul")


class NodeBackhaul(ABC):
    """How this node talks to other nodes (batman-adv since Phase 3)."""

    @abstractmethod
    def forward_to_zone(self, zone_id: int, packet: bytes) -> None:
        """Forward a packet to the node(s) serving the given zone."""

    @abstractmethod
    def broadcast_to_all_nodes(self, packet: bytes) -> None:
        """Flood a packet (e.g. a venue-wide announcement) to every node."""

    def on_receive(self, callback: Callable[[str, bytes], None]) -> None:
        """Register callback(peer_id, raw) for packets arriving from other
        nodes. Default is a no-op: the Phase 2 stub had no receive direction,
        and a backhaul-less node simply never gets called back."""


class LoggingStubBackhaul(NodeBackhaul):
    """No-op stand-in from Phase 2: logs what a real backhaul would do.

    Still used by tests and by nodes running without a backhaul radio.
    """

    def forward_to_zone(self, zone_id: int, packet: bytes) -> None:
        log.info(
            "would forward %d-byte packet to zone %d — no backhaul yet (Phase 3)",
            len(packet), zone_id,
        )

    def broadcast_to_all_nodes(self, packet: bytes) -> None:
        log.info(
            "would broadcast %d-byte packet to all nodes — no backhaul yet (Phase 3)",
            len(packet),
        )
