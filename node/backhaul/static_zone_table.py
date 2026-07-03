"""Static zone → node-IP table for the 3-node Phase 3 test deployment.

Hand-wired on purpose: with 3 nodes there is nothing to gain from building
the dynamic zone-routing table yet, but proving cross-zone forwarding works
now de-risks it. Zone N is served by the node whose bat0 address is
10.77.0.N — the addressing scheme scripts/setup_batman.sh assigns.

REPLACED IN PHASE 7: this whole module gives way to a dynamic zone-routing
table (node/backhaul/dynamic_zone_table.py) maintained via node heartbeats;
nothing outside backhaul/ may import the table directly so that swap stays
local.
"""

ZONE_TO_NODE_IP: dict[int, str] = {
    1: "10.77.0.1",
    2: "10.77.0.2",
    3: "10.77.0.3",
}


def ip_for_zone(zone_id: int) -> str | None:
    """The backhaul IP of the node serving zone_id, or None if unknown.

    Callers must handle None — an unknown zone falls back to flooding all
    nodes (Technical Reference §5.2) rather than dropping the message.
    """
    return ZONE_TO_NODE_IP.get(zone_id)
