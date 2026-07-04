"""Platform-independent GATT server core.

Everything the MeshLink node needs from a BLE peripheral that is NOT tied to
a specific Bluetooth stack lives here: per-peer frame reassembly, connection
bookkeeping, and outbound framing/chunking. Platform backends (BlueZ on
Linux, CoreBluetooth on macOS) subclass this and implement only the radio
plumbing — advertising, characteristic registration, and raw chunk delivery.

Keeping shared behaviour in this base class is the porting contract: a
"common" change made while developing on one platform lands here and applies
to every backend unchanged.
"""
import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

from node import config
from node.ble.framing import FrameAssembler, chunk, frame

log = logging.getLogger("meshlink.ble")


class GattServerBase(ABC):
    """Facade the transport layer uses, independent of the Bluetooth stack.

    Callbacks:
      on_packet(peer_id, packet)   — complete reassembled inbound packet
      on_disconnect(peer_id)       — a tracked central disconnected

    peer_id is an opaque per-central string; its format is backend-specific
    (a D-Bus device path on BlueZ, a CBCentral UUID on CoreBluetooth).
    """

    def __init__(self) -> None:
        self._assemblers: dict[str, FrameAssembler] = {}
        self._connected: set[str] = set()
        self.on_packet: Optional[Callable[[str, bytes], None]] = None
        self.on_disconnect: Optional[Callable[[str], None]] = None

    # -- backend plumbing ------------------------------------------------------

    @abstractmethod
    def start(self) -> None:
        """Power up, register the GATT tree, and begin advertising."""

    @abstractmethod
    def run_forever(self) -> None:
        """Block running the backend's event loop until stop() is called."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the event loop; safe to call from a callback."""

    @abstractmethod
    def _notify_chunk(self, data: bytes) -> None:
        """Deliver one already-framed chunk out the TX characteristic."""

    # -- inbound (shared) ------------------------------------------------------

    def _handle_chunk(self, peer_id: str, data: bytes) -> None:
        """Feed raw bytes written to RX into the per-peer reassembler."""
        self._connected.add(peer_id)
        assembler = self._assemblers.setdefault(peer_id, FrameAssembler())
        try:
            packets = assembler.feed(data)
        except ValueError as exc:
            log.warning("dropping corrupt stream from %s: %s", peer_id, exc)
            return
        for packet in packets:
            if self.on_packet is not None:
                self.on_packet(peer_id, packet)

    def _peer_connected(self, peer_id: str) -> None:
        self._connected.add(peer_id)
        log.info("central connected: %s", peer_id)

    def _peer_disconnected(self, peer_id: str) -> None:
        self._connected.discard(peer_id)
        self._assemblers.pop(peer_id, None)
        if self.on_disconnect is not None:
            self.on_disconnect(peer_id)
        log.info("central disconnected: %s", peer_id)

    # -- outbound (shared) -----------------------------------------------------

    def send_packet(self, peer_id: str, packet: bytes) -> None:
        """Notify a framed packet out the TX characteristic.

        peer_id is accepted for interface symmetry but the notification fans
        out to every subscribed central — a shared GATT characteristic cannot
        unicast; the phone-side msg_id dedup discards copies not meant for it.
        """
        for piece in chunk(frame(packet), config.BLE_NOTIFY_CHUNK):
            self._notify_chunk(piece)

    def peers(self) -> list[str]:
        return sorted(self._connected)
