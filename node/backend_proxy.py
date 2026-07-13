"""Phone→backend proxy: phones reach the backend through their node.

Phones at the venue often have no internet — the venue WiFi is a closed
network and BLE reaches only the node. The node, however, already talks to
the backend (organiser key at boot, heartbeats, directory sync) over
whichever uplink its platform has: a macOS dev node sits on a regular WiFi
LAN, while a Pi node's only IP network is the batman-adv mesh, where the
backend lives at its mesh address (config.BACKEND_CHANNEL selects between
the two; config.BACKEND_URL is the result). This service opens that same
uplink to the app's account/ticketing HTTP flows.

Wire contract (app half: meshlink-app lib/transport/backend_proxy.dart) —
same demux pattern as the MLPP1 telemetry ping, magic prefix ``MLBP1``:

    phone → node: MLBP1{"t":"req","id":"a3","method":"POST",
                        "path":"/tickets","headers":{"authorization":"…"},
                        "body":"{…}"}
    node → phone: MLBP1{"t":"res","id":"a3","status":201,"body":"{…}"}
                  MLBP1{"t":"res","id":"a3","status":0,"error":"…"}

status 0 means the node itself could not complete the call (no uplink,
timeout); real backend rejections keep their HTTP status so the app's
retryable/definitive split (attestation_flow.dart) keeps working.

Only allowlisted, app-facing backend paths are forwarded — never the
organiser/operator surface (/heartbeat, /admin, /dashboard, /noc). Requests
run on a small worker pool so a slow backend can neither block the relay's
receive path nor let one phone pile up unbounded threads. The reply goes
back on whichever transport the request arrived on (transport.send routes
by peer id).
"""
import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from node.core import Transport

log = logging.getLogger("meshlink.backend_proxy")

BACKEND_PROXY_MAGIC = b"MLBP1"

# App-facing Supabase surface only: GoTrue auth, PostgREST tables/RPCs, and
# Edge Functions (ticket→token chain). RLS is the real gate server-side; the
# allowlist just keeps the proxy from becoming a generic tunnel.
ALLOWED_PATH_PREFIXES = (
    "/auth/v1/",
    "/rest/v1/",
    "/functions/v1/",
    "/health",
)

# PostgREST uses PATCH (profile key rebind) and DELETE alongside GET/POST.
ALLOWED_METHODS = ("GET", "POST", "PATCH", "DELETE")

# Response bodies ride BLE at worst (180-byte notify chunks) — cap them so a
# runaway endpoint can't wedge a phone's link for minutes.
MAX_RESPONSE_BYTES = 256 * 1024

_COMPACT = {"separators": (",", ":")}


def is_backend_proxy_frame(frame: bytes) -> bool:
    """The demux rule: proxy frames — and only they — start with MLBP1.

    A real mesh packet starts with a random 16-byte msg_id, so a false
    match is ~2^-40 (same argument as the MLPP1 telemetry demux).
    """
    return frame.startswith(BACKEND_PROXY_MAGIC)


def decode(frame: bytes) -> Optional[dict]:
    """Lenient decode of a proxy frame's JSON body; None → drop silently."""
    if not is_backend_proxy_frame(frame):
        return None
    try:
        body = json.loads(frame[len(BACKEND_PROXY_MAGIC):].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or "t" not in body:
        return None
    return body


def encode_request(req_id: str, method: str, path: str,
                   body: Optional[str] = None,
                   headers: Optional[dict] = None) -> bytes:
    """Reference encoder for the phone side of the wire contract (tests)."""
    frame = {"t": "req", "id": req_id, "method": method, "path": path}
    if headers:
        frame["headers"] = headers
    if body is not None:
        frame["body"] = body
    return BACKEND_PROXY_MAGIC + json.dumps(frame, **_COMPACT).encode()


def encode_response(req_id, status: int, body: Optional[str] = None,
                    error: Optional[str] = None) -> bytes:
    frame = {"t": "res", "id": req_id, "status": status}
    if body is not None:
        frame["body"] = body
    if error is not None:
        frame["error"] = error
    return BACKEND_PROXY_MAGIC + json.dumps(frame, **_COMPACT).encode()


def path_allowed(path) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and ".." not in path
        and path.startswith(ALLOWED_PATH_PREFIXES)
    )


class BackendProxyService:
    """Serves demuxed MLBP1 request frames against the backend uplink."""

    def __init__(
        self,
        transport: Transport,
        base_url: str,
        timeout_s: float = 10.0,
        max_workers: int = 4,
    ):
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="backend-proxy",
        )
        log.info("backend proxy serving %s (timeout %.0fs)",
                 self._base_url, timeout_s)

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def handle_frame(self, peer_id: str, frame: bytes) -> None:
        """Consume one demuxed proxy frame (relay hands these over)."""
        body = decode(frame)
        if body is None or body.get("t") != "req":
            return
        try:
            self._pool.submit(self._serve, peer_id, body)
        except RuntimeError:
            pass  # shutting down

    # -- worker side ---------------------------------------------------------

    def _serve(self, peer_id: str, req: dict) -> None:
        reply = self._execute(req)
        try:
            self._transport.send(peer_id, reply)
        except Exception:
            # The phone may have walked away mid-request; never let that
            # bubble into the pool.
            log.warning("proxy reply to %s failed", peer_id, exc_info=True)

    def _execute(self, req: dict) -> bytes:
        req_id = req.get("id")
        method = req.get("method")
        path = req.get("path")
        if method not in ALLOWED_METHODS:
            return encode_response(req_id, 0, error=f"method not allowed: {method}")
        if not path_allowed(path):
            log.info("proxy refused path %r", path)
            return encode_response(req_id, 0, error="path not allowed")

        body = req.get("body")
        data = body.encode() if isinstance(body, str) else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        # Only the headers Supabase needs may cross: the bearer token
        # (Authorization), the project anon/API key (apikey), and PostgREST's
        # Prefer for upsert/return behaviour. Everything else is dropped.
        raw_headers = req.get("headers")
        if isinstance(raw_headers, dict):
            lowered = {k.lower(): v for k, v in raw_headers.items()
                       if isinstance(k, str)}
            for name, sent_as in (("authorization", "Authorization"),
                                  ("apikey", "apikey"),
                                  ("prefer", "Prefer")):
                value = lowered.get(name)
                if isinstance(value, str):
                    headers[sent_as] = value

        request = urllib.request.Request(
            f"{self._base_url}{path}", data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as resp:
                text = resp.read(MAX_RESPONSE_BYTES + 1)
                status = resp.status
        except urllib.error.HTTPError as exc:
            # A backend rejection is a *successful* proxy call — forward it.
            text = exc.read(MAX_RESPONSE_BYTES + 1)
            status = exc.code
        except Exception as exc:
            log.warning("proxy %s %s failed: %s", method, path, exc)
            return encode_response(req_id, 0, error="backend unreachable")

        if len(text) > MAX_RESPONSE_BYTES:
            log.warning("proxy %s %s response over %d bytes — refused",
                        method, path, MAX_RESPONSE_BYTES)
            return encode_response(req_id, 0, error="response too large")
        log.info("proxy %s %s -> %d (%d bytes)", method, path, status, len(text))
        return encode_response(req_id, status, body=text.decode("utf-8", "replace"))
