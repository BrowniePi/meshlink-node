"""Node configuration."""
import json
import os
import socket
import sys
from pathlib import Path

# The zone this node serves. Each node is deployed with a distinct zone via
# MESHLINK_ZONE_ID (replacing Phase 2's single hardcoded value); nodes learn
# which zone every *other* node serves at runtime via zone-sync gossip
# (node/backhaul/dynamic_zone_table.py), so only our own zone is configured.
NODE_ZONE_ID = int(os.environ.get("MESHLINK_ZONE_ID", "1"))

# Human-readable zone label for the organiser dashboard ("Main Stage",
# "Food Court"…). A zone can be served by several nodes, so this names the
# *zone*, not the node — every node in a zone should carry the same value.
# Purely cosmetic: routing keys off NODE_ZONE_ID alone.
NODE_ZONE_NAME = os.environ.get("MESHLINK_ZONE_NAME", f"Zone {NODE_ZONE_ID}")

# Backhaul (batman-adv) networking — see node/backhaul/batman_backhaul.py.
BACKHAUL_UDP_PORT = int(os.environ.get("MESHLINK_BACKHAUL_PORT", "19788"))  # 0x4D4C — "ML"
BACKHAUL_BROADCAST_ADDR = "10.77.0.255"  # mesh subnet broadcast (10.77.0.0/24)


def parse_addr(text):
    """Parse "host" or "host:port" into a batman_backhaul Addr.

    A bare host keeps the caller's default port; "host:port" pins both — the
    latter lets several dev nodes share one machine on loopback. IPv4 only
    (the backhaul socket is AF_INET), so a lone ":" always means host:port.
    """
    text = text.strip()
    host, sep, port = text.rpartition(":")
    return (host, int(port)) if sep else text


def parse_zone_table(text):
    """Parse "1=host,2=host:port,…" into a {zone_id: Addr} table."""
    table = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        zone, _, addr = entry.partition("=")
        table[int(zone)] = parse_addr(addr)
    return table


# Operator-pinned zone seed for development on machines without batman-adv
# (e.g. Mac dev nodes on a plain LAN or on loopback) that have no zone-sync
# gossip to learn from. Loaded as never-expiring fallback entries in the
# dynamic table; a fresh learned entry for the same zone always wins. Same
# override pattern as MESHLINK_BLE_BACKEND. Unset → learn everything live.
#   MESHLINK_ZONE_TABLE="1=192.168.1.10,2=192.168.1.11:19789"
#   MESHLINK_BACKHAUL_BROADCAST_ADDR="192.168.1.255"
_zone_table_env = os.environ.get("MESHLINK_ZONE_TABLE")
BACKHAUL_ZONE_TABLE = parse_zone_table(_zone_table_env) if _zone_table_env else None

_broadcast_env = os.environ.get("MESHLINK_BACKHAUL_BROADCAST_ADDR")
if _broadcast_env:
    BACKHAUL_BROADCAST_ADDR = parse_addr(_broadcast_env)

# Phase 7 dynamic zone-routing table (node/backhaul/dynamic_zone_table.py +
# zone_sync.py), replacing Phase 3's static ZONE_TO_NODE_IP. See
# docs/phase7-node-decisions.md.
#   - The address we announce to peers as serving our zone. Defaults to the
#     batman-adv zone N ↔ 10.77.0.N scheme; dev nodes on a LAN/loopback
#     override it. Doubles as the self-echo filter for our own broadcasts.
_advertise_env = os.environ.get("MESHLINK_BACKHAUL_ADVERTISE_ADDR")
BACKHAUL_ADVERTISE_ADDR = (
    parse_addr(_advertise_env) if _advertise_env else f"10.77.0.{NODE_ZONE_ID}"
)
#   - A learned entry is forgotten after this long without a re-announcement;
#     default tolerates ~3 missed announcements at the 60 s heartbeat cadence.
ZONE_ENTRY_TTL_S = float(os.environ.get("MESHLINK_ZONE_ENTRY_TTL_S", "180"))

# Phase 5 — meshlink-backend integration. The backend is only ever touched
# off the message path: one organiser-key fetch at boot plus the 60 s
# heartbeat, and (backend-via-node) proxied app requests. Mesh messages never
# leave the venue network.
BACKEND_BASE_URL = os.environ.get("MESHLINK_BACKEND_URL", "http://127.0.0.1:8000")

# Which uplink carries that backend traffic. macOS dev nodes sit on a regular
# WiFi LAN and reach BACKEND_BASE_URL directly; a deployed Pi's only IP
# network is the batman-adv mesh (bat0), so the backend is reached at its
# mesh address — the machine hosting (or NATing to) it, joined to the mesh.
# "auto" picks by platform; MESHLINK_BACKEND_CHANNEL=wifi_lan|batman forces.
BACKEND_CHANNEL = os.environ.get("MESHLINK_BACKEND_CHANNEL", "auto").lower()
if BACKEND_CHANNEL == "auto":
    BACKEND_CHANNEL = "wifi_lan" if sys.platform == "darwin" else "batman"

# Backend address on the batman-adv mesh. Convention: zone nodes take
# 10.77.0.<zone> (scripts/setup_batman.sh), the backend host takes .254.
BACKEND_BATMAN_URL = os.environ.get(
    "MESHLINK_BACKEND_BATMAN_URL", "http://10.77.0.254:8000"
)

# The effective URL every node→backend caller uses (organiser key fetch,
# heartbeat, directory sync, phone backend-proxy).
BACKEND_URL = BACKEND_BATMAN_URL if BACKEND_CHANNEL == "batman" else BACKEND_BASE_URL

# Event this node is deployed for; attestation tokens for any other eid are
# rejected at pipeline step 7. Must match the event_id the app purchases
# tickets for (backend caps it at 25 chars to keep tokens small).
EVENT_ID = os.environ.get("MESHLINK_EVENT_ID", "test-event-001")

# Organiser Ed25519 public key (64 hex chars). Normally fetched from the
# backend at startup and cached to disk; the env var skips the fetch entirely
# (tests, air-gapped bench setups).
ORGANISER_PUBKEY = os.environ.get("MESHLINK_ORGANISER_PUBKEY")
ORGANISER_KEY_CACHE = Path(
    os.environ.get("MESHLINK_ORGANISER_KEY_CACHE",
                   Path(__file__).resolve().parent.parent / "organiser_pubkey.hex")
)

# Heartbeat reporting (node → backend, fire-and-forget).
NODE_ID = os.environ.get("MESHLINK_NODE_ID", socket.gethostname())
HEARTBEAT_INTERVAL_S = float(os.environ.get("MESHLINK_HEARTBEAT_INTERVAL_S", "60"))

# Phase 5 friendship extension — node-served location.
#   - Node's own signing identity for LOCATION_RESPONSE packets (generated on
#     first boot, core identity format).
NODE_IDENTITY_PATH = Path(
    os.environ.get("MESHLINK_NODE_IDENTITY",
                   Path(__file__).resolve().parent.parent / "node_identity.json")
)
#   - Offline-capable user directory copy, synced from the backend's
#     /directory/sync at the heartbeat cadence (node/directory/cache.py).
DIRECTORY_CACHE = Path(
    os.environ.get("MESHLINK_DIRECTORY_CACHE",
                   Path(__file__).resolve().parent.parent / "directory_cache.json")
)
#   - Per (requester, target) floor between served location queries: even a
#     friend with a valid capability token cannot poll a position into a
#     fine-grained track (query-side of the retention invariant).
LOCATION_QUERY_MIN_INTERVAL_S = float(
    os.environ.get("MESHLINK_LOCATION_QUERY_MIN_INTERVAL_S", "60")
)

# Phase 7 phone telemetry ping (node/monitoring/phone_ping.py): how often
# each connected phone is asked for its location and battery. Reports age
# out after 3 missed pings and ride the heartbeat's phone_telemetry block.
PHONE_PING_INTERVAL_S = float(os.environ.get("MESHLINK_PHONE_PING_INTERVAL_S", "90"))

# Grace period between a phone connecting and its first ping: the app needs
# a moment after the link comes up before it can answer, so firing the ping
# the instant the transport reports the peer would just waste it.
PHONE_PING_CONNECT_DELAY_S = float(
    os.environ.get("MESHLINK_PHONE_PING_CONNECT_DELAY_S", "3")
)

# Node identity/location carried on every phone ping, so the app can label
# "who is this node and where is it" without a separate lookup. Lives in its
# own small JSON file (not env vars) so an operator can hand-edit it on the
# device — auto-created with placeholder values on first boot, same pattern
# as NODE_IDENTITY_PATH. lat/lon are the node's own fixed position (a phone
# app or map lookup, entered once per deployment), not a GPS reading.
NODE_INFO_PATH = Path(
    os.environ.get("MESHLINK_NODE_INFO",
                    Path(__file__).resolve().parent.parent / "node_info.json")
)


def _load_node_info(path: Path) -> dict:
    if not path.exists():
        path.write_text(json.dumps(
            {"node_name": "MeshLink Node", "lat": None, "lon": None}, indent=2,
        ) + "\n")
    with path.open() as f:
        return json.load(f)


_node_info = _load_node_info(NODE_INFO_PATH)
NODE_NAME = _node_info.get("node_name") or "MeshLink Node"
NODE_LAT = _node_info.get("lat")
NODE_LON = _node_info.get("lon")

# Phase 6 — phone-facing WiFi listener (node/transport/wifi_transport.py).
# Default binds the hostapd AP address (scripts/setup_hostapd.sh); dev
# machines without that interface degrade to BLE-only automatically, and
# "off" disables the listener outright. Must match the app's
# MESHLINK_WIFI_NODE_HOST/PORT dart-defines.
WIFI_LISTEN = os.environ.get("MESHLINK_WIFI_LISTEN", "10.78.0.1:7800")

# Whether main.py itself brings up the phone-facing AP (node/wifi_ap).
# "auto": provision on macOS (the Internet Sharing dev-parity backend, the
# WiFi twin of MESHLINK_BLE_BACKEND=corebluetooth) but NOT on the Pi, where
# scripts/setup_hostapd.sh + systemd own the AP out of band — Phase 6 Tasks
# 1-2 deliberately kept main.py out of AP setup. "on"/"off" force it either
# way. Backend override: MESHLINK_AP_BACKEND=hostapd|internet_sharing.
WIFI_AP_PROVISION = os.environ.get("MESHLINK_AP_PROVISION", "auto").lower()

# GATT layout — must match meshlink-app lib/transport/ble_transport.dart.
MESH_SERVICE_UUID = "4d455348-4c49-4e4b-0001-000000000001"
RX_CHAR_UUID = "4d455348-4c49-4e4b-0002-000000000002"  # centrals write inbound
TX_CHAR_UUID = "4d455348-4c49-4e4b-0003-000000000003"  # node notifies outbound

BLE_LOCAL_NAME = "MeshLink"

# Notification chunk size. The app requests ATT MTU 247 (usable 244) and iOS
# negotiates at least 185 (usable 182); 180 stays under both. Lower this to
# 20 if a central with the minimum ATT MTU (23) must be supported.
BLE_NOTIFY_CHUNK = 180
