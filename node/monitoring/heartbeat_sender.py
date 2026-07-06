"""Heartbeat reporting: node → backend, every 60 s, fire-and-forget.

The node half of the Phase 5 monitoring task (backend ingestion is
POST /heartbeat). This is the only node→internet traffic — mesh messages
stay on the venue network. A beat that fails (backend down, no uplink,
timeout) is logged and skipped; it must never affect relay service.
"""
import json
import logging
import threading
import time
import urllib.request

from node.backhaul.base import NodeBackhaul
from node.core import Transport

log = logging.getLogger("meshlink.heartbeat")


class HeartbeatSender:
    def __init__(
        self,
        node_id: str,
        zone_id: int,
        base_url: str,
        transport: Transport,
        backhaul: NodeBackhaul,
        interval_s: float = 60.0,
        timeout_s: float = 3.0,
    ):
        self._node_id = node_id
        self._zone_id = zone_id
        self._url = f"{base_url.rstrip('/')}/heartbeat"
        self._transport = transport
        self._backhaul = backhaul
        self._interval_s = interval_s
        self._timeout_s = timeout_s
        self._started_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, name="heartbeat", daemon=True,
        )
        self._thread.start()
        log.info("heartbeat every %.0f s → %s (node_id=%s)",
                 self._interval_s, self._url, self._node_id)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._timeout_s + 1)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.beat()

    def beat(self) -> None:
        """Send one heartbeat; failures are logged, never raised."""
        body = {
            "node_id": self._node_id,
            "uptime_s": int(time.monotonic() - self._started_at),
            "connected_phone_count": len(self._transport.list_peers()),
            "zone_id": self._zone_id,
            "batman_peer_count": self._backhaul.peer_count(),
        }
        req = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s):
                pass
        except Exception as exc:
            log.warning("heartbeat failed (relay unaffected): %s", exc)
        else:
            log.info("heartbeat sent: %s", body)
