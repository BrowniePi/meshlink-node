"""Node-cached user directory, synced from meshlink-backend.

Read-only copy of `{username, curve25519_pub, ed25519_pub}` for the event's
attendees, used to resolve the target key for capability-token verification
(location/authz.py) and usernames in friend requests. Same offline-first
pattern as the organiser key (node/attestation/organiser_key.py): refreshed
from GET /directory/sync at the heartbeat cadence and on demand, persisted to
disk, and fully functional with no internet during the event.

This cache holds public identity only — usernames and public keys. It never
holds locations; the stable-identity → location mapping lives solely in
location/store.py, node-internal (invariant 2).
"""
import json
import logging
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from node.core import pubkey_id

log = logging.getLogger("meshlink.directory")


class DirectoryCache:
    def __init__(self, base_url: str, cache_path: Path, event_id: str,
                 refresh_interval_s: float = 60.0, timeout_s: float = 5.0,
                 anon_key: str = ""):
        # PostgREST view: returns a plain JSON array of directory rows.
        # (event_id was accepted-but-unused by the old backend; PostgREST
        # rejects unknown filter params, so it stays out of the URL.)
        self._url = (f"{base_url.rstrip('/')}/rest/v1/directory"
                     f"?select=username,curve25519_pub,ed25519_pub,created_at"
                     f"&order=username")
        self._anon_key = anon_key
        self._cache_path = cache_path
        self._interval_s = refresh_interval_s
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._by_username: dict[str, dict] = {}
        self._by_ed_id: dict[bytes, dict] = {}  # pubkey_id(ed25519) -> user
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if cache_path.exists():
            try:
                self._index(json.loads(cache_path.read_text()))
                log.info("directory cache loaded from disk: %d users",
                         len(self._by_username))
            except (ValueError, KeyError) as exc:
                log.warning("stale directory cache unreadable (%s) — will re-sync", exc)

    def _index(self, users: list[dict]) -> None:
        by_username = {u["username"]: u for u in users}
        by_ed_id = {pubkey_id(bytes.fromhex(u["ed25519_pub"])): u for u in users}
        with self._lock:
            self._by_username = by_username
            self._by_ed_id = by_ed_id

    def refresh(self) -> bool:
        """One sync attempt. Keeps the previous copy on any failure — the
        node must keep answering with its offline copy when the backhaul has
        no internet."""
        headers = {"apikey": self._anon_key} if self._anon_key else {}
        try:
            req = urllib.request.Request(self._url, headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                users = json.load(resp)
            if not isinstance(users, list):
                raise ValueError("directory response is not a list")
        except (OSError, ValueError, KeyError) as exc:
            log.warning("directory sync failed (%s) — keeping %d cached users",
                        exc, len(self._by_username))
            return False
        self._index(users)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(users))
        log.info("directory synced: %d users", len(users))
        return True

    def start(self) -> None:
        """Refresh now and then at the heartbeat cadence."""
        self.refresh()
        self._thread = threading.Thread(target=self._loop, name="directory-sync",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.refresh()

    def by_username(self, username: str) -> Optional[dict]:
        with self._lock:
            return self._by_username.get(username)

    def by_ed25519_id(self, ed_pubkey_id: bytes) -> Optional[dict]:
        """Resolve an 8-byte truncated Ed25519 key hash (capability token
        issuer/grantee field) to a directory entry."""
        with self._lock:
            return self._by_ed_id.get(ed_pubkey_id)

    def user_count(self) -> int:
        with self._lock:
            return len(self._by_username)
