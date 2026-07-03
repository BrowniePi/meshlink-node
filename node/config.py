"""Node configuration."""
import os

# The zone this node serves. Phase 3: each of the 3 test nodes is deployed
# with a distinct zone via MESHLINK_ZONE_ID (1, 2, or 3 — matching its
# entry in node/backhaul/static_zone_table.py), replacing Phase 2's single
# hardcoded value. Phase 7 replaces this env-var hand-wiring with dynamic
# zone assignment at deployment.
NODE_ZONE_ID = int(os.environ.get("MESHLINK_ZONE_ID", "1"))

# Backhaul (batman-adv) networking — see node/backhaul/batman_backhaul.py.
BACKHAUL_UDP_PORT = 19788  # 0x4D4C — "ML"
BACKHAUL_BROADCAST_ADDR = "10.77.0.255"  # mesh subnet broadcast (10.77.0.0/24)

# GATT layout — must match meshlink-app lib/transport/ble_transport.dart.
MESH_SERVICE_UUID = "4d455348-4c49-4e4b-0001-000000000001"
RX_CHAR_UUID = "4d455348-4c49-4e4b-0002-000000000002"  # centrals write inbound
TX_CHAR_UUID = "4d455348-4c49-4e4b-0003-000000000003"  # node notifies outbound

BLE_LOCAL_NAME = "MeshLink"

# Notification chunk size. The app requests ATT MTU 247 (usable 244) and iOS
# negotiates at least 185 (usable 182); 180 stays under both. Lower this to
# 20 if a central with the minimum ATT MTU (23) must be supported.
BLE_NOTIFY_CHUNK = 180
