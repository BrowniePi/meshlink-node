"""Organiser key bootstrap: fetch-once, disk-cache fallback, hard failure."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from node.attestation.organiser_key import load_organiser_pubkey

PUB_HEX = "ab" * 32


@pytest.fixture
def backend():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/attestation/public-key"
            body = json.dumps({"public_key": PUB_HEX, "key_algorithm": "Ed25519",
                               "jwt_algorithm": "EdDSA"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_fetches_and_caches_to_disk(backend, tmp_path):
    cache = tmp_path / "organiser_pubkey.hex"
    assert load_organiser_pubkey(backend, cache) == PUB_HEX
    assert cache.read_text().strip() == PUB_HEX


def test_backend_down_falls_back_to_cache(tmp_path):
    cache = tmp_path / "organiser_pubkey.hex"
    cache.write_text(PUB_HEX + "\n")
    assert load_organiser_pubkey("http://127.0.0.1:9", cache) == PUB_HEX


def test_backend_down_and_no_cache_fails_hard(tmp_path):
    with pytest.raises(RuntimeError, match="cannot obtain organiser public key"):
        load_organiser_pubkey("http://127.0.0.1:9", tmp_path / "missing.hex")
