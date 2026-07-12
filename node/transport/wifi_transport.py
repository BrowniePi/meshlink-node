"""Phone-facing WiFi transport (Phase 6) — the same Transport contract as BLE.

Listens for TCP connections from phones that joined the hostapd AP
(10.78.0.x). Each connection is one peer, held open for its lifetime;
packets travel in both directions with the same 2-byte big-endian length
prefix the BLE path uses (node/ble/framing.py, mirrored by the app's
transports). Peer ids are "wifi:<ip>:<port>" so a multi-transport dispatcher
can route sends by prefix.

If the listen address can't be bound (dev machine without the AP interface,
or WiFi disabled for a deployment), the transport logs and stays inert —
Phase 6 is strictly additive, so a node without WiFi must behave exactly
like Phase 5.
"""
import logging
import socket
import threading
from typing import Callable, Optional

from node.ble.framing import FrameAssembler, frame
from node.core import MAX_PACKET, Transport

log = logging.getLogger("meshlink.wifi")

WIFI_PEER_PREFIX = "wifi:"


class WifiTransport(Transport):
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: Optional[socket.socket] = None
        self._callback: Optional[Callable[[str, bytes], None]] = None
        self._connect_callback: Optional[Callable[[str], None]] = None
        self._running = False
        # peer_id -> live connection; guarded by _lock (accept/reader threads
        # add/remove, the relay's GLib thread iterates via list_peers/send).
        self._conns: dict[str, socket.socket] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self._host, self._port))
        except OSError as exc:
            # No AP interface on this machine — run BLE-only (Phase 5 parity).
            server.close()
            log.warning("cannot bind %s:%d (%s) — WiFi serving disabled",
                        self._host, self._port, exc)
            return
        server.listen(16)
        self._port = server.getsockname()[1]  # resolves port 0 (tests)
        self._server = server
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True,
                         name="wifi-accept").start()
        log.info("listening for phones on %s:%d", self._host, self._port)

    def stop(self) -> None:
        self._running = False
        if self._server is not None:
            self._server.close()
            self._server = None
        with self._lock:
            conns = list(self._conns.values())
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass

    def send(self, peer_id: str, data: bytes) -> None:
        with self._lock:
            conn = self._conns.get(peer_id)
        if conn is None:
            log.warning("send to unknown/disconnected peer %s", peer_id)
            return
        try:
            conn.sendall(frame(data))
        except OSError:
            # Peer vanishing mid-send must never take down the relay loop;
            # its reader thread notices the dead socket and drops it.
            log.warning("send to %s failed", peer_id, exc_info=True)

    def on_receive(self, callback: Callable[[str, bytes], None]) -> None:
        self._callback = callback

    def on_connect(self, callback: Callable[[str], None]) -> None:
        """See BleTransport.on_connect — same opt-in, non-Transport-contract hook."""
        self._connect_callback = callback

    def list_peers(self) -> list[str]:
        with self._lock:
            return list(self._conns)

    @property
    def port(self) -> int:
        return self._port

    @property
    def active(self) -> bool:
        """True when the listener is up (bind succeeded and not stopped)."""
        return self._running

    # -- internal ------------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server.accept()
            except OSError:
                break  # stop() closed the listener
            peer_id = f"{WIFI_PEER_PREFIX}{addr[0]}:{addr[1]}"
            with self._lock:
                self._conns[peer_id] = conn
            log.info("phone connected: %s", peer_id)
            if self._connect_callback is not None:
                try:
                    self._connect_callback(peer_id)
                except Exception:
                    log.exception("connect callback failed for %s", peer_id)
            threading.Thread(target=self._reader, args=(peer_id, conn),
                             daemon=True, name=f"wifi-{peer_id}").start()

    def _reader(self, peer_id: str, conn: socket.socket) -> None:
        assembler = FrameAssembler()
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                try:
                    packets = assembler.feed(data)
                except ValueError as exc:
                    # Corrupt stream: no way to resync a length-prefixed
                    # stream, so drop the connection like BLE drops a link.
                    log.warning("corrupt stream from %s: %s", peer_id, exc)
                    break
                for packet in packets:
                    self._dispatch(peer_id, packet)
        except OSError:
            pass
        finally:
            with self._lock:
                self._conns.pop(peer_id, None)
            try:
                conn.close()
            except OSError:
                pass
            log.info("phone disconnected: %s", peer_id)

    def _dispatch(self, peer_id: str, packet: bytes) -> None:
        # Same cheap sanity gate as BleTransport; the pipeline does the rest.
        if not packet or len(packet) > MAX_PACKET:
            log.warning("discarding malformed %d-byte packet from %s",
                        len(packet), peer_id)
            return
        if self._callback is None:
            return
        try:
            self._callback(peer_id, packet)
        except Exception:
            log.exception("receive callback failed for packet from %s", peer_id)
