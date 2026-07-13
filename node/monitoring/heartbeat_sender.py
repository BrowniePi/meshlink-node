"""Heartbeat reporting: node → backend, every 60 s, fire-and-forget.

The node half of the Phase 5 monitoring task (backend ingestion is
POST /heartbeat), widened in Phase 7 to the v2 payload the organiser
dashboard reads: zone id+name, battery, per-transport phone lists, relay
traffic counters, and host health. Payload template lives in
docs/heartbeat-payload.md — keep the two in sync.

This is the only node→internet traffic — mesh messages stay on the venue
network, and no message *content* ever appears here, only counters. A beat
that fails (backend down, no uplink, timeout) is logged and skipped; it
must never affect relay service.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone
import urllib.request

from node.backhaul.base import NodeBackhaul
from node.core import Transport
from node.monitoring.battery import read_battery
from node.monitoring.phone_ping import PhonePingService
from node.monitoring.system_stats import read_system_stats
from node.transport.wifi_transport import WIFI_PEER_PREFIX

log = logging.getLogger("meshlink.heartbeat")

HEARTBEAT_VERSION = 2


class HeartbeatSender:
    def __init__(
        self,
        node_id: str,
        zone_id: int,
        zone_name: str,
        base_url: str,
        transport: Transport,
        backhaul: NodeBackhaul,
        relay=None,  # anything with .stats() -> dict; None → no relay block
        phone_ping: PhonePingService | None = None,
        interval_s: float = 60.0,
        timeout_s: float = 3.0,
        anon_key: str = "",
    ):
        self._node_id = node_id
        self._zone_id = zone_id
        self._zone_name = zone_name
        # PostgREST insert into the heartbeats table: {node_id, payload}.
        self._url = f"{base_url.rstrip('/')}/rest/v1/heartbeats"
        self._anon_key = anon_key
        self._transport = transport
        self._backhaul = backhaul
        self._relay = relay
        self._phone_ping = phone_ping
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

    def build_payload(self) -> dict:
        """The v2 heartbeat body (see docs/heartbeat-payload.md)."""
        peers = self._transport.list_peers()
        wifi_peers = [p for p in peers if p.startswith(WIFI_PEER_PREFIX)]
        return {
            "heartbeat_version": HEARTBEAT_VERSION,
            "node_id": self._node_id,
            "zone_id": self._zone_id,
            "zone_name": self._zone_name,
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "uptime_s": int(time.monotonic() - self._started_at),
            "connected_phone_count": len(peers),
            "batman_peer_count": self._backhaul.peer_count(),
            "phones": {
                "ble_count": len(peers) - len(wifi_peers),
                "wifi_count": len(wifi_peers),
                "peers": peers,
            },
            "battery": read_battery(),
            "relay": self._relay.stats() if self._relay is not None else None,
            "phone_telemetry": (
                {"reports": self._phone_ping.reports()}
                if self._phone_ping is not None else None
            ),
            "system": read_system_stats(),
        }

    def beat(self) -> None:
        """Send one heartbeat; failures are logged, never raised."""
        body = self.build_payload()
        headers = {"Content-Type": "application/json",
                   "Prefer": "return=minimal"}
        if self._anon_key:
            headers["apikey"] = self._anon_key
            headers["Authorization"] = f"Bearer {self._anon_key}"
        req = urllib.request.Request(
            self._url,
            data=json.dumps({"node_id": self._node_id, "payload": body}).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s):
                pass
        except Exception as exc:
            log.warning("heartbeat failed (relay unaffected): %s", exc)
        else:
            log.info("heartbeat sent: %s", body)
