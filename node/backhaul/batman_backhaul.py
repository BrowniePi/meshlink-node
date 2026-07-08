"""batman-adv backhaul: UDP over the bat0 mesh interface (Phase 3, dynamic in 7).

Concrete NodeBackhaul filling the Phase 2 stub. batman-adv (configured by
scripts/setup_batman.sh) presents the whole node mesh as one Layer-2 network
on bat0, so "forward to the node serving zone N" is just a UDP datagram to
that node's mesh IP — multi-hop routing, link failures, and re-convergence
are batman-adv's problem, not ours.

Wire behaviour:
- forward_to_zone: unicast to the zone's node per the DynamicZoneTable;
  an unknown zone falls back to flooding all nodes (Technical Reference
  §5.2) instead of dropping. The table is filled at runtime by zone-sync
  (Phase 7), replacing Phase 3's hand-wired static_zone_table.
- broadcast_to_all_nodes: one datagram to the mesh subnet's broadcast
  address; batman-adv delivers it to every node.
- broadcast_control: same broadcast, but the payload is a zone-sync
  announcement tagged with ZONE_SYNC_MAGIC.
- receive: a listener thread demuxes inbound datagrams by that magic prefix
  — control frames go to the zone-sync callback (on_control), everything
  else to the relay callback (on_receive), which runs it through the same
  pipeline as BLE traffic (dedup stops loops and our own broadcasts echoing
  back).

Send failures are logged and the packet dropped — a flaky peer node must
never crash the relay serving local phones.
"""
import logging
import socket
import threading
from typing import Callable

from node import config
from node.backhaul.base import NodeBackhaul
from node.backhaul.dynamic_zone_table import DynamicZoneTable
from node.backhaul.zone_sync import ZONE_SYNC_MAGIC

log = logging.getLogger("meshlink.backhaul")

Endpoint = tuple[str, int]

# Zone-table values, own_addr, and broadcast_addr may be "ip" (port defaults
# to config.BACKHAUL_UDP_PORT) or an explicit ("ip", port) — the latter lets
# tests run several nodes on 127.0.0.1.
Addr = str | Endpoint


def _endpoint(addr: Addr) -> Endpoint:
    return (addr, config.BACKHAUL_UDP_PORT) if isinstance(addr, str) else addr


class BatmanBackhaul(NodeBackhaul):
    def __init__(
        self,
        zone_id: int,
        table: DynamicZoneTable | None = None,
        own_addr: Addr | None = None,
        broadcast_addr: Addr = config.BACKHAUL_BROADCAST_ADDR,
        bind: Endpoint = ("0.0.0.0", config.BACKHAUL_UDP_PORT),
    ):
        self._zone_id = zone_id
        # The routing table is a live, gossip-fed collaborator shared with the
        # ZoneSync driving it (see node/main.py); default to an empty one so a
        # backhaul stood up alone still routes (flooding until it learns).
        self._table = (
            table if table is not None else DynamicZoneTable(own_zone_id=zone_id)
        )
        # Where peers should reach us — announced by zone-sync and used to
        # filter our own broadcasts echoing back. Defaults to the batman-adv
        # zone N ↔ 10.77.0.N scheme (scripts/setup_batman.sh).
        self._own_addr: Addr = own_addr if own_addr is not None else f"10.77.0.{zone_id}"
        self._broadcast_addr = broadcast_addr
        self._data_cb: Callable[[str, bytes], None] | None = None
        self._control_cb: Callable[[str, bytes], None] | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(bind)

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def peer_count(self) -> int:
        # Live count from the gossip-fed table: zones other than ours currently
        # routable, so it rises and falls as nodes join and age out.
        return self._table.peer_count()

    # -- NodeBackhaul (send direction) ------------------------------------

    def forward_to_zone(self, zone_id: int, packet: bytes) -> None:
        if zone_id == self._zone_id:
            log.warning("asked to forward to own zone %d — local relay's job, "
                        "dropping", zone_id)
            return
        addr = self._table.addr_for(zone_id)
        if addr is None:
            log.warning("no node known for zone %d — flooding all nodes instead",
                        zone_id)
            self.broadcast_to_all_nodes(packet)
            return
        self._send(packet, _endpoint(addr), f"zone {zone_id}")

    def broadcast_to_all_nodes(self, packet: bytes) -> None:
        self._send(packet, _endpoint(self._broadcast_addr), "all nodes")

    def broadcast_control(self, frame: bytes) -> None:
        self._send(frame, _endpoint(self._broadcast_addr), "control")

    def _send(self, packet: bytes, endpoint: Endpoint, label: str) -> None:
        try:
            self._sock.sendto(packet, endpoint)
        except OSError as exc:
            log.error("backhaul send to %s (%s) failed, packet dropped: %s",
                      label, endpoint, exc)
        else:
            log.info("forwarded %d-byte packet to %s via %s",
                     len(packet), label, endpoint)

    # -- Receive direction --------------------------------------------------

    def on_receive(self, callback: Callable[[str, bytes], None]) -> None:
        self._data_cb = callback

    def on_control(self, callback: Callable[[str, bytes], None]) -> None:
        self._control_cb = callback

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._recv_loop, name="backhaul-recv", daemon=True,
        )
        self._thread.start()
        log.info("backhaul listening on udp/%d (zone %d)", self.port, self._zone_id)

    def stop(self) -> None:
        self._running = False
        self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _recv_loop(self) -> None:
        own_endpoint = _endpoint(self._own_addr)
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except OSError:
                break  # socket closed by stop()
            if addr == own_endpoint:
                continue  # our own subnet broadcast echoing back
            peer_id = f"backhaul:{addr[0]}:{addr[1]}"
            # Demux off the relay path: a zone-sync control frame goes to the
            # gossip layer, never to the relay/phones (Phase 7 decisions doc).
            if data.startswith(ZONE_SYNC_MAGIC):
                self._deliver(self._control_cb, peer_id, data, "control")
            else:
                self._deliver(self._data_cb, peer_id, data, "receive")

    def _deliver(self, callback: Callable[[str, bytes], None] | None,
                 peer_id: str, data: bytes, kind: str) -> None:
        if callback is None:
            return
        try:
            callback(peer_id, data)
        except Exception:
            log.exception("backhaul %s handler failed for %s", kind, peer_id)
