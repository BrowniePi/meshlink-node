"""Zone-sync: keep the DynamicZoneTable current by gossiping over the backhaul.

Each node broadcasts a small announcement — "node N serves zone Z at addr A" —
to every other node once per interval (the heartbeat interval, per the
Technical Reference: re-sync the routing table at each heartbeat). Receiving
nodes feed it into their DynamicZoneTable and prune anything that has gone
quiet. That is the whole join/leave mechanism: a new node's first announcement
teaches everyone its zone; a departed node's entry simply ages out.

Announcements ride the same backhaul socket as mesh traffic but are tagged with
a magic prefix so the backhaul can demux them to on_control() instead of the
relay pipeline — they are control frames, not messages, and must never reach a
phone. The frame is deliberately tiny JSON so it costs nothing on the mesh.
"""
import json
import logging
import threading

from node.backhaul.base import NodeBackhaul
from node.backhaul.dynamic_zone_table import Addr, DynamicZoneTable

log = logging.getLogger("meshlink.zonesync")

# Leading marker that tags a datagram as a zone-sync control frame rather than
# a mesh packet. A real packet is >=131 bytes starting with a random msg_id, so
# a collision on these 5 bytes is ~2^-32 and would only cost one dropped frame.
ZONE_SYNC_MAGIC = b"MLZS1"


def encode_announcement(node_id: str, zone_id: int, addr: Addr) -> bytes:
    """Serialise this node's announcement into a control frame."""
    # Tuples don't survive JSON as tuples; normalise ("host", port) to a list
    # and back on decode so addr comparisons in the table stay consistent.
    wire_addr = list(addr) if isinstance(addr, tuple) else addr
    body = {"node_id": node_id, "zone_id": zone_id, "addr": wire_addr}
    return ZONE_SYNC_MAGIC + json.dumps(body).encode()


def decode_announcement(frame: bytes) -> dict | None:
    """Parse a control frame back to {node_id, zone_id, addr}, or None if it
    isn't a well-formed announcement (never raises — a malformed frame from a
    misbehaving peer must not disturb the receive loop)."""
    if not frame.startswith(ZONE_SYNC_MAGIC):
        return None
    try:
        body = json.loads(frame[len(ZONE_SYNC_MAGIC):])
        node_id = str(body["node_id"])
        zone_id = int(body["zone_id"])
        raw_addr = body["addr"]
    except (ValueError, KeyError, TypeError):
        log.warning("dropping malformed zone-sync frame (%d bytes)", len(frame))
        return None
    addr: Addr = tuple(raw_addr) if isinstance(raw_addr, list) else raw_addr
    return {"node_id": node_id, "zone_id": zone_id, "addr": addr}


class ZoneSync:
    def __init__(
        self,
        backhaul: NodeBackhaul,
        table: DynamicZoneTable,
        node_id: str,
        zone_id: int,
        own_addr: Addr,
        interval_s: float = 60.0,
    ):
        self._backhaul = backhaul
        self._table = table
        self._node_id = node_id
        self._zone_id = zone_id
        self._own_addr = own_addr
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        backhaul.on_control(self._on_control)

    def start(self) -> None:
        # Announce immediately so a joining node populates its neighbours
        # (and vice versa) without waiting a full interval.
        self.announce_now()
        self._thread = threading.Thread(
            target=self._loop, name="zone-sync", daemon=True,
        )
        self._thread.start()
        log.info("zone-sync every %.0f s — node %s serves zone %d at %s",
                 self._interval_s, self._node_id, self._zone_id, self._own_addr)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.announce_now()
            forgotten = self._table.forget_stale()
            if forgotten:
                log.info("zone-sync forgot silent zones %s", forgotten)

    def announce_now(self) -> None:
        """Broadcast this node's own zone assignment to every other node."""
        frame = encode_announcement(self._node_id, self._zone_id, self._own_addr)
        self._backhaul.broadcast_control(frame)

    def _on_control(self, peer_id: str, frame: bytes) -> None:
        ann = decode_announcement(frame)
        if ann is None:
            return
        if ann["node_id"] == self._node_id:
            return  # our own announcement echoing back off the broadcast
        self._table.learn(ann["zone_id"], ann["addr"])
        log.info("zone-sync learned zone %d → %s (from node %s)",
                 ann["zone_id"], ann["addr"], ann["node_id"])
