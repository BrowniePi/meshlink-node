"""Node-to-node backhaul interface — deliberately unimplemented at Phase 2.

There is exactly one node right now, so there is nothing to mesh. This
interface exists so the node's routing logic already speaks in terms of
"hand this to another node" — Phase 3's batman-adv backhaul module will be
new code implementing this interface, not a rewrite of how the node routes.

Do not add networking code here in Phase 2.
"""
import logging
from abc import ABC, abstractmethod

log = logging.getLogger("meshlink.backhaul")


class NodeBackhaul(ABC):
    """How this node talks to other nodes. Implemented in Phase 3 (batman-adv)."""

    @abstractmethod
    def forward_to_zone(self, zone_id: int, packet: bytes) -> None:
        """Forward a packet to the node(s) serving the given zone."""

    @abstractmethod
    def broadcast_to_all_nodes(self, packet: bytes) -> None:
        """Flood a packet (e.g. a venue-wide announcement) to every node."""


class LoggingStubBackhaul(NodeBackhaul):
    """No-op stand-in until Phase 3: logs what a real backhaul would do."""

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
