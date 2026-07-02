"""Node configuration."""

# The zone this node serves. Hardcoded for Phase 2 — there is one node and
# one zone, and NodeRelay treats every message as local to this zone (no
# multi-zone routing logic exists yet). Phase 7 replaces this constant with
# dynamic zone assignment at deployment.
NODE_ZONE_ID = 1

# GATT layout — must match meshlink-app lib/transport/ble_transport.dart.
MESH_SERVICE_UUID = "4d455348-4c49-4e4b-0001-000000000001"
RX_CHAR_UUID = "4d455348-4c49-4e4b-0002-000000000002"  # centrals write inbound
TX_CHAR_UUID = "4d455348-4c49-4e4b-0003-000000000003"  # node notifies outbound

BLE_LOCAL_NAME = "MeshLink-Node"

# Notification chunk size. The app requests ATT MTU 247 (usable 244) and iOS
# negotiates at least 185 (usable 182); 180 stays under both. Lower this to
# 20 if a central with the minimum ATT MTU (23) must be supported.
BLE_NOTIFY_CHUNK = 180
