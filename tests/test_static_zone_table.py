"""Static zone-to-node table for the 3-node Phase 3 deployment.

The table itself is replaced by dynamic zone routing in Phase 7; these
tests pin the Phase 3 contract: 3 distinct zones, each mapped to a distinct
node IP on the mesh subnet, unknown zones signalled with None.
"""
import subprocess
from pathlib import Path

from node import config
from node.backhaul.static_zone_table import ZONE_TO_NODE_IP, ip_for_zone

MESH_PREFIX = "10.77.0."


def test_three_nodes_with_distinct_zones_and_ips():
    assert sorted(ZONE_TO_NODE_IP) == [1, 2, 3]
    ips = list(ZONE_TO_NODE_IP.values())
    assert len(set(ips)) == 3


def test_zone_ids_are_valid_unicast_zones():
    for zone_id in ZONE_TO_NODE_IP:
        assert 0 < zone_id < 0xFFFF  # 0x0000 reserved, 0xFFFF broadcast


def test_ips_follow_the_setup_batman_addressing_scheme():
    # Zone N ↔ 10.77.0.N, as assigned by scripts/setup_batman.sh.
    for zone_id, ip in ZONE_TO_NODE_IP.items():
        assert ip == f"{MESH_PREFIX}{zone_id}"


def test_unknown_zone_returns_none():
    assert ip_for_zone(9) is None


def test_node_zone_id_configurable_per_node_via_env():
    script = "from node import config; print(config.NODE_ZONE_ID)"
    for zone in ("1", "2", "3"):
        out = subprocess.run(
            ["python3", "-c", script],
            env={"MESHLINK_ZONE_ID": zone, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert out.stdout.strip() == zone


def test_this_nodes_zone_is_in_the_table():
    assert config.NODE_ZONE_ID in ZONE_TO_NODE_IP
