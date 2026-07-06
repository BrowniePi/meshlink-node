"""batman-adv backhaul: UDP over the bat0 mesh interface (Phase 3).

Concrete NodeBackhaul filling the Phase 2 stub. batman-adv (configured by
scripts/setup_batman.sh) presents the whole node mesh as one Layer-2 network
on bat0, so "forward to the node serving zone N" is just a UDP datagram to
that node's mesh IP — multi-hop routing, link failures, and re-convergence
are batman-adv's problem, not ours.

Wire behaviour:
- forward_to_zone: unicast to the zone's node per the static zone table;
  an unknown zone falls back to flooding all nodes (Technical Reference
  §5.2) instead of dropping.
- broadcast_to_all_nodes: one datagram to the mesh subnet's broadcast
  address; batman-adv delivers it to every node.
- receive: a listener thread hands inbound packets to the callback
  registered via on_receive(); the receiving node runs them through the
  same relay pipeline as BLE traffic (dedup stops loops and our own
  broadcasts echoing back).

Send failures are logged and the packet dropped — a flaky peer node must
never crash the relay serving local phones.
"""
import logging
import socket
import threading
from typing import Callable

from node import config
from node.backhaul import static_zone_table
from node.backhaul.base import NodeBackhaul

log = logging.getLogger("meshlink.backhaul")

Endpoint = tuple[str, int]

# Zone-table values and broadcast_addr may be "ip" (port defaults to
# config.BACKHAUL_UDP_PORT) or an explicit ("ip", port) — the latter lets
# tests run several nodes on 127.0.0.1.
Addr = str | Endpoint


def _endpoint(addr: Addr) -> Endpoint:
    return (addr, config.BACKHAUL_UDP_PORT) if isinstance(addr, str) else addr


class BatmanBackhaul(NodeBackhaul):
    def __init__(
        self,
        zone_id: int,
        zone_table: dict[int, Addr] | None = None,
        broadcast_addr: Addr = config.BACKHAUL_BROADCAST_ADDR,
        bind: Endpoint = ("0.0.0.0", config.BACKHAUL_UDP_PORT),
    ):
        self._zone_id = zone_id
        self._zone_table = (
            zone_table if zone_table is not None
            else dict(static_zone_table.ZONE_TO_NODE_IP)
        )
        self._broadcast_addr = broadcast_addr
        self._callback: Callable[[str, bytes], None] | None = None
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
        # Other nodes known to the zone table. On a live batman-adv mesh the
        # ground truth would be `batctl n`; the static table is what this
        # node's routing actually uses, so heartbeats report that.
        return sum(1 for zone in self._zone_table if zone != self._zone_id)

    # -- NodeBackhaul (send direction) ------------------------------------

    def forward_to_zone(self, zone_id: int, packet: bytes) -> None:
        if zone_id == self._zone_id:
            log.warning("asked to forward to own zone %d — local relay's job, "
                        "dropping", zone_id)
            return
        addr = self._zone_table.get(zone_id)
        if addr is None:
            log.warning("no node known for zone %d — flooding all nodes instead",
                        zone_id)
            self.broadcast_to_all_nodes(packet)
            return
        self._send(packet, _endpoint(addr), f"zone {zone_id}")

    def broadcast_to_all_nodes(self, packet: bytes) -> None:
        self._send(packet, _endpoint(self._broadcast_addr), "all nodes")

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
        self._callback = callback

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
        own = self._zone_table.get(self._zone_id)
        own_endpoint = _endpoint(own) if own is not None else None
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except OSError:
                break  # socket closed by stop()
            if addr == own_endpoint:
                continue  # our own subnet broadcast echoing back
            if self._callback is None:
                continue
            try:
                self._callback(f"backhaul:{addr[0]}:{addr[1]}", data)
            except Exception:
                log.exception("backhaul receive handler failed for %s", addr)
