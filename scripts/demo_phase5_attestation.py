"""Phase 5 milestone demo driver: attestation-gated relay across two nodes.

Runs the demo half that needs no phone hardware: two real NodeRelay processes
(this one and a --node2 subprocess it spawns) joined by the real BatmanBackhaul
UDP path on loopback — the Mac dev-node pattern from Phase 3. Only the BLE
radio is replaced, by an in-memory phone transport; every packet still runs
the full vendored-core pipeline with real Ed25519 signatures and real EdDSA
attestation tokens fetched from a running meshlink-backend.

Sequence (see meshlink-backend docs/demos/phase5-attestation-gated-relay.md):
  1. both nodes fetch the organiser public key at startup (one HTTP call each);
  2. Device A buys a ticket + fetches a token over HTTP, presents it to node 1
     — node 1 caches the sender, swallows the message, spreads it to node 2;
  3. Device A's text relays node 1 → node 2 → phone C;
  4. Device B (no token) is refused at step 7, as are expired / wrong-event /
     forged / stolen-token presentations.

Usage: backend running on --base-url (default http://127.0.0.1:8000), then
    python scripts/demo_phase5_attestation.py
"""
import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from node.attestation import MSG_TYPE_ATTESTATION_PRESENT
from node.attestation.organiser_key import load_organiser_pubkey
from node.backhaul.batman_backhaul import BatmanBackhaul
from node.backhaul.dynamic_zone_table import DynamicZoneTable
from node.core import AttestationCache, Transport
from node.monitoring.heartbeat_sender import HeartbeatSender
from node.relay import BROADCAST_ZONE, NodeRelay

from identity.keygen import generate_keypair  # vendored meshlink-core
from identity.signing import build_signed_packet

MSG_TYPE_TEXT = 0x01
NODE1_PORT = 19801
NODE2_PORT = 19802
ZONE_TABLE = {1: ("127.0.0.1", NODE1_PORT), 2: ("127.0.0.1", NODE2_PORT)}

log = logging.getLogger("demo")


class PhoneTransport(Transport):
    """In-memory stand-in for the BLE layer: scripted devices inject packets,
    and everything the node fans out to a phone is recorded."""

    def __init__(self, phones):
        self.received = {phone: [] for phone in phones}
        self._callback = None

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, peer_id, data):
        self.received[peer_id].append(data)
        log.info("phone %s received %d-byte packet", peer_id, len(data))

    def on_receive(self, callback):
        self._callback = callback

    def list_peers(self):
        return list(self.received)

    def inject(self, phone, raw):
        """A phone writes a packet to the node (BLE RX in production)."""
        self._callback(phone, raw)


class Device:
    """A scripted phone: real meshlink-core identity + packet signing."""

    def __init__(self, name):
        self.name = name
        self.identity = generate_keypair()
        self.ephem_id = secrets.token_bytes(16)

    @property
    def pubkey_hex(self):
        return self.identity.public_key.hex()

    def packet(self, msg_type, payload):
        return build_signed_packet(
            self.identity, ephem_id=self.ephem_id, ttl=5, spray_l=1,
            zone_id=BROADCAST_ZONE, msg_type=msg_type, payload=payload,
        )


def call(base, method, path, body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def start_node(zone_id, base_url, phones):
    cache = Path(tempfile.mkdtemp()) / "organiser_pubkey.hex"
    pub_hex = load_organiser_pubkey(base_url, cache)  # the one boot-time call
    attestation = AttestationCache(bytes.fromhex(pub_hex), "test-event-001")
    transport = PhoneTransport(phones)
    # ZONE_TABLE seeds the dynamic table as operator-pinned entries — this
    # demo has no zone-sync gossip, so the seed is the whole routing table.
    backhaul = BatmanBackhaul(
        zone_id=zone_id,
        table=DynamicZoneTable(own_zone_id=zone_id, seed=dict(ZONE_TABLE)),
        own_addr=ZONE_TABLE[zone_id],
        broadcast_addr=ZONE_TABLE[2 if zone_id == 1 else 1],
        bind=("127.0.0.1", ZONE_TABLE[zone_id][1]),
    )
    relay = NodeRelay(
        transport=transport, backhaul=backhaul, zone_id=zone_id,
        attestation=attestation,
    )
    backhaul.start()
    relay.start()
    return transport, backhaul, relay


def run_node2(base_url):
    """Subprocess: node 2 (zone 2) serving phone C. Runs for 15 s."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    transport, backhaul, _ = start_node(2, base_url, ["phoneC"])
    time.sleep(15)
    texts = [p for p in transport.received["phoneC"] if p[72] == MSG_TYPE_TEXT]
    log.info("node2 summary: phoneC received %d text packet(s): %s",
             len(texts),
             [bytes(p[75:75 + int.from_bytes(p[73:75], "big")])
              .decode("utf-8", "replace") for p in texts])
    backhaul.stop()


def make_bad_tokens(organiser_seed_hex, device, base_url):
    """Craft the refusal-case tokens. Uses the organiser signing key file
    (same-host demo) for the expired/wrong-event/stolen cases; a random
    non-organiser key for the forged case."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    organiser = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(organiser_seed_hex))
    now = int(time.time())
    good = {"sub": device.pubkey_hex, "eid": "test-event-001",
            "iat": now, "exp": now + 3600}
    return {
        "expired token": jwt.encode({**good, "exp": now - 60}, organiser,
                                    algorithm="EdDSA"),
        "wrong-event token": jwt.encode({**good, "eid": "other-event-999"},
                                        organiser, algorithm="EdDSA"),
        "forged token (non-organiser signer)": jwt.encode(
            good, Ed25519PrivateKey.generate(), algorithm="EdDSA"),
        "stolen token (sub != sender_key)": jwt.encode(
            {**good, "sub": secrets.token_hex(32)}, organiser, algorithm="EdDSA"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--node2", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--organiser-key-file", default=None,
                        help="organiser seed hex file for crafting refusal-case "
                             "tokens (default: ../meshlink_backend/var/organiser_key.hex)")
    args = parser.parse_args()

    if args.node2:
        run_node2(args.base_url)
        return

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    node2_log = open("demo-node2.log", "w")
    node2 = subprocess.Popen(
        [sys.executable, __file__, "--node2", "--base-url", args.base_url],
        stdout=node2_log, stderr=subprocess.STDOUT,
    )
    try:
        print("\n=== node 1 (zone 1) starting: organiser key fetch + relay ===")
        transport, backhaul, _ = start_node(1, args.base_url, ["phoneA", "phoneB"])
        heartbeat = HeartbeatSender(
            node_id="demo-node-1", zone_id=1, base_url=args.base_url,
            transport=transport, backhaul=backhaul, interval_s=1.0,
        )
        heartbeat.start()
        time.sleep(2.0)  # let node 2 finish its own startup fetch

        print("\n=== Device A: simulated ticket purchase + token fetch ===")
        device_a = Device("A")
        ticket = call(args.base_url, "POST", "/tickets",
                      {"event_id": "test-event-001",
                       "buyer_pubkey": device_a.pubkey_hex})
        print(f"ticket: {ticket['ticket_id']}")
        issued = call(args.base_url, "POST", "/attestation/token",
                      {"ticket_id": ticket["ticket_id"],
                       "event_id": "test-event-001",
                       "device_pubkey": device_a.pubkey_hex})
        token = issued["token"]
        print(f"token ({len(token.encode())} bytes ≤ 321 payload cap): {token}")

        print("\n=== Device A presents its token to node 1 ===")
        transport.inject("phoneA",
                         device_a.packet(MSG_TYPE_ATTESTATION_PRESENT, token.encode()))
        time.sleep(1.0)  # presentation spreads to node 2 over the backhaul

        print("\n=== Device A (attested) sends a text — relays via backhaul to node 2 / phone C ===")
        transport.inject("phoneA",
                         device_a.packet(MSG_TYPE_TEXT,
                                         b"Meet at south gate (Phase 5 demo)"))
        time.sleep(1.0)

        print("\n=== Device B (no token) sends a text — refused at step 7 ===")
        device_b = Device("B")
        transport.inject("phoneB",
                         device_b.packet(MSG_TYPE_TEXT, b"Sybil says hi"))

        print("\n=== Refusal cases: invalid presentations ===")
        key_file = Path(args.organiser_key_file
                        or Path(__file__).resolve().parents[2].parent
                        / "meshlink_backend" / "var" / "organiser_key.hex")
        for label, bad in make_bad_tokens(key_file.read_text().strip(),
                                          device_b, args.base_url).items():
            print(f"--- presenting {label} ---")
            transport.inject("phoneB",
                             device_b.packet(MSG_TYPE_ATTESTATION_PRESENT, bad.encode()))
        print("--- Device B retries a normal text (still unattested) ---")
        transport.inject("phoneB", device_b.packet(MSG_TYPE_TEXT, b"still here"))

        print("\n=== Heartbeats: two beats then GET latest ===")
        time.sleep(2.5)
        latest = call(args.base_url, "GET", "/heartbeat/demo-node-1/latest")
        print(f"latest heartbeat: {latest}")

        heartbeat.stop()
        node2.wait(timeout=20)
        backhaul.stop()
    finally:
        if node2.poll() is None:
            node2.kill()
        node2_log.close()

    print("\n=== node 2 log (subprocess) ===")
    print(Path("demo-node2.log").read_text())


if __name__ == "__main__":
    main()
