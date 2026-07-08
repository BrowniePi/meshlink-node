"""Phone telemetry ping: node → phone every 2 minutes, phone answers back.

The node half of the Phase 7 telemetry loop (app half:
meshlink-app docs/phone-ping-app-spec.md). Every interval the node sends
``MLPP1{"t":"ping"}`` to each connected phone over the transport it is
already connected on; the phone answers with its location and battery:
``MLPP1{"t":"pong","lat":…,"lon":…,"battery":…,"charging":…}``.

The node keeps only the latest report per phone, ages it out after 3 missed
pings, and folds the survivors into the heartbeat's ``phone_telemetry``
(docs/heartbeat-payload.md). Telemetry frames never leave the venue mesh —
NodeRelay demuxes them off the relay pipeline before any packet parsing, so
they are neither relayed to other phones nor forwarded as message content.
"""
import json
import logging
import threading
import time
from typing import Callable, Optional

from node.core import Transport

log = logging.getLogger("meshlink.phone_ping")

PHONE_PING_MAGIC = b"MLPP1"

_COMPACT = {"separators": (",", ":")}


def encode_ping() -> bytes:
    return PHONE_PING_MAGIC + json.dumps({"t": "ping"}, **_COMPACT).encode()


def encode_pong(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    battery: Optional[int] = None,
    charging: Optional[bool] = None,
) -> bytes:
    """Reference encoder for the phone side of the wire contract (tests)."""
    body = {"t": "pong", "lat": lat, "lon": lon, "battery": battery}
    if charging is not None:
        body["charging"] = charging
    return PHONE_PING_MAGIC + json.dumps(body, **_COMPACT).encode()


def is_telemetry_frame(frame: bytes) -> bool:
    """The demux rule: telemetry frames — and only they — start with MLPP1.

    A real mesh packet starts with a random 16-byte msg_id, so a false
    match is ~2^-40.
    """
    return frame.startswith(PHONE_PING_MAGIC)


def decode(frame: bytes) -> Optional[dict]:
    """Lenient decode of a telemetry frame's JSON body.

    Returns the body dict, or None for anything that isn't a JSON object
    carrying a "t" key — such frames are dropped silently, per the wire
    contract. Unknown extra keys pass through untouched.
    """
    if not is_telemetry_frame(frame):
        return None
    try:
        body = json.loads(frame[len(PHONE_PING_MAGIC):].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or "t" not in body:
        return None
    return body


def _as_float(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_int(value) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class PhonePingService:
    """Pings every connected phone each interval and collects the answers.

    Reports live for 3 × interval (3 missed pings) and then age out, so a
    phone that disconnects or stops answering disappears from the heartbeat
    on its own. [clock] is injectable for deterministic aging tests.
    """

    def __init__(
        self,
        transport: Transport,
        interval_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._transport = transport
        self._interval_s = interval_s
        self._clock = clock
        self._reports: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def report_ttl_s(self) -> float:
        return 3 * self._interval_s

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="phone-ping", daemon=True,
        )
        self._thread.start()
        log.info("phone ping every %.0f s (reports live %.0f s)",
                 self._interval_s, self.report_ttl_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.ping_all()

    def ping_all(self) -> None:
        """Send one ping to every connected phone; failures never raise."""
        ping = encode_ping()
        for peer in self._transport.list_peers():
            try:
                self._transport.send(peer, ping)
            except Exception:
                # A peer vanishing mid-ping must never take down the loop.
                log.warning("ping to %s failed", peer, exc_info=True)

    def handle_frame(self, peer_id: str, frame: bytes) -> None:
        """Consume one demuxed telemetry frame (relay hands these over).

        Missing/mistyped lat, lon, battery are stored as None; frames that
        aren't valid JSON or lack "t" are dropped silently.
        """
        body = decode(frame)
        if body is None or body.get("t") != "pong":
            return
        charging = body.get("charging")
        report = {
            "lat": _as_float(body.get("lat")),
            "lon": _as_float(body.get("lon")),
            "battery": _as_int(body.get("battery")),
            "charging": charging if isinstance(charging, bool) else None,
            "received_at": self._clock(),
        }
        with self._lock:
            self._reports[peer_id] = report
        log.info(
            "phone-ping report from %s: lat=%s lon=%s battery=%s charging=%s",
            peer_id, report["lat"], report["lon"], report["battery"],
            report["charging"],
        )

    def reports(self) -> list[dict]:
        """Latest un-aged report per phone, for the heartbeat payload."""
        now = self._clock()
        cutoff = now - self.report_ttl_s
        with self._lock:
            stale = [p for p, r in self._reports.items()
                     if r["received_at"] < cutoff]
            for peer in stale:
                del self._reports[peer]
            return [
                {
                    "peer_id": peer,
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "battery": r["battery"],
                    "charging": r["charging"],
                    "age_s": int(now - r["received_at"]),
                }
                for peer, r in sorted(self._reports.items())
            ]
