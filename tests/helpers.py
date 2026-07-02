"""Test helpers: a fake Transport and packet building via the vendored core."""
import importlib.util
from pathlib import Path

from node.core import Transport

# Reuse meshlink-core's packet builder without shadowing this repo's `tests`
# package (both repos have a top-level tests/).
_core_helpers_path = (
    Path(__file__).resolve().parents[1]
    / "vendor" / "meshlink-core" / "tests" / "helpers.py"
)
_spec = importlib.util.spec_from_file_location("core_test_helpers", _core_helpers_path)
_core_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core_helpers)

build_packet = _core_helpers.build_packet


class FakeTransport(Transport):
    """In-memory Transport capturing sends; drives receives synchronously."""

    def __init__(self, peers: list[str]):
        self.peers = peers
        self.sent: list[tuple[str, bytes]] = []
        self.started = False
        self._callback = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def send(self, peer_id: str, data: bytes) -> None:
        self.sent.append((peer_id, data))

    def on_receive(self, callback) -> None:
        self._callback = callback

    def list_peers(self) -> list[str]:
        return list(self.peers)

    def deliver(self, peer_id: str, data: bytes) -> None:
        """Simulate an inbound packet from peer_id."""
        self._callback(peer_id, data)
