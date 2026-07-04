"""Node-side BLE transport — the same Transport contract the app satisfies.

Wraps a GATT server backend so the relay pipeline sends/receives through the
exact abstraction meshlink-core defined in Phase 0 (transport/base.py). The
relay logic needs zero changes to run over BLE instead of the Phase 0 socket
transport — that symmetry, on both ends of the network, is the point.
"""
import logging
from typing import Callable, Optional

from node.core import MAX_PACKET, Transport

log = logging.getLogger("meshlink.transport")


class BleTransport(Transport):
    def __init__(self, server) -> None:
        """server: a node.ble.base.GattServerBase backend (or a test double with
        the same start/stop/send_packet/peers/on_packet/on_disconnect surface)."""
        self._server = server
        self._callback: Optional[Callable[[str, bytes], None]] = None
        server.on_packet = self._handle_packet
        server.on_disconnect = self._handle_disconnect

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop()

    def send(self, peer_id: str, data: bytes) -> None:
        try:
            self._server.send_packet(peer_id, data)
        except Exception:
            # A peer vanishing mid-send must never take down the relay loop.
            log.warning("send to %s failed", peer_id, exc_info=True)

    def on_receive(self, callback: Callable[[str, bytes], None]) -> None:
        self._callback = callback

    def list_peers(self) -> list[str]:
        return self._server.peers()

    # -- GattServer callbacks --------------------------------------------------

    def _handle_packet(self, peer_id: str, packet: bytes) -> None:
        # Cheap sanity gate; the pipeline does full validation. Anything the
        # framing layer reassembled beyond MAX_PACKET is stream corruption.
        if not packet or len(packet) > MAX_PACKET:
            log.warning("discarding malformed %d-byte packet from %s",
                        len(packet), peer_id)
            return
        if self._callback is None:
            return
        try:
            self._callback(peer_id, packet)
        except Exception:
            # Malformed data must not crash the GLib main loop.
            log.exception("receive callback failed for packet from %s", peer_id)

    def _handle_disconnect(self, peer_id: str) -> None:
        log.info("peer %s disconnected", peer_id)
