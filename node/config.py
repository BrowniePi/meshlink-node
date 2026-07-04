"""Node configuration."""
import os

# The zone this node serves. Phase 3: each of the 3 test nodes is deployed
# with a distinct zone via MESHLINK_ZONE_ID (1, 2, or 3 — matching its
# entry in node/backhaul/static_zone_table.py), replacing Phase 2's single
# hardcoded value. Phase 7 replaces this env-var hand-wiring with dynamic
# zone assignment at deployment.
NODE_ZONE_ID = int(os.environ.get("MESHLINK_ZONE_ID", "1"))

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


# Backhaul address overrides for development on machines without batman-adv
# (e.g. Mac dev nodes on a plain LAN or on loopback). Same override pattern as
# MESHLINK_BLE_BACKEND. Unset → the batman-adv 10.77.0.x scheme above.
#   MESHLINK_ZONE_TABLE="1=192.168.1.10,2=192.168.1.11:19789"
#   MESHLINK_BACKHAUL_BROADCAST_ADDR="192.168.1.255"
_zone_table_env = os.environ.get("MESHLINK_ZONE_TABLE")
BACKHAUL_ZONE_TABLE = parse_zone_table(_zone_table_env) if _zone_table_env else None

_broadcast_env = os.environ.get("MESHLINK_BACKHAUL_BROADCAST_ADDR")
if _broadcast_env:
    BACKHAUL_BROADCAST_ADDR = parse_addr(_broadcast_env)

# GATT layout — must match meshlink-app lib/transport/ble_transport.dart.
MESH_SERVICE_UUID = "4d455348-4c49-4e4b-0001-000000000001"
RX_CHAR_UUID = "4d455348-4c49-4e4b-0002-000000000002"  # centrals write inbound
TX_CHAR_UUID = "4d455348-4c49-4e4b-0003-000000000003"  # node notifies outbound

BLE_LOCAL_NAME = "MeshLink"

# Notification chunk size. The app requests ATT MTU 247 (usable 244) and iOS
# negotiates at least 185 (usable 182); 180 stays under both. Lower this to
# 20 if a central with the minimum ATT MTU (23) must be supported.
BLE_NOTIFY_CHUNK = 180
