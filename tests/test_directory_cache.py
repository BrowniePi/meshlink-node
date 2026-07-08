"""Node directory cache: backend sync, offline fallback, key-hash lookup."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import node.core  # noqa: F401
from node.core import pubkey_id
from node.directory.cache import DirectoryCache

USERS = [
    {"username": "ada", "curve25519_pub": "aa" * 32, "ed25519_pub": "ab" * 32},
    {"username": "grace", "curve25519_pub": "ba" * 32, "ed25519_pub": "bb" * 32},
]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path.startswith("/directory/sync")
        body = json.dumps({"users": USERS, "count": len(USERS)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve_once():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_sync_and_lookups(tmp_path):
    server, url = serve_once()
    try:
        cache = DirectoryCache(url, tmp_path / "dir.json", "test-event-001")
        assert cache.refresh() is True
    finally:
        server.shutdown()

    assert cache.user_count() == 2
    assert cache.by_username("ada")["ed25519_pub"] == "ab" * 32
    assert cache.by_username("nobody") is None
    # token verification resolves the target via the truncated key hash
    entry = cache.by_ed25519_id(pubkey_id(bytes.fromhex("bb" * 32)))
    assert entry["username"] == "grace"
    assert cache.by_ed25519_id(b"\x00" * 8) is None


def test_offline_boot_uses_disk_copy(tmp_path):
    path = tmp_path / "dir.json"
    path.write_text(json.dumps(USERS))
    # backend unreachable: constructor loads the disk copy, refresh() fails soft
    cache = DirectoryCache("http://127.0.0.1:1", path, "test-event-001")
    assert cache.user_count() == 2
    assert cache.refresh() is False
    assert cache.by_username("grace") is not None  # previous copy kept


def test_failed_first_sync_leaves_empty_but_alive(tmp_path):
    cache = DirectoryCache("http://127.0.0.1:1", tmp_path / "dir.json", "e")
    assert cache.refresh() is False
    assert cache.user_count() == 0
    assert cache.by_username("ada") is None
