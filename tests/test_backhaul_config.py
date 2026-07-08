"""Backhaul address overrides for dev nodes without batman-adv (e.g. Mac).

Pure-parser tests (config.parse_addr / parse_zone_table) plus an end-to-end
check that MESHLINK_ZONE_TABLE actually reaches BatmanBackhaul's routing —
the batman-adv 10.77.0.x scheme only exists on the mesh, so a Mac dev node
has to be pointed at real LAN IPs or loopback ports instead.
"""
import subprocess
from pathlib import Path

from node import config


def test_parse_addr_bare_host_keeps_default_port():
    assert config.parse_addr("192.168.1.10") == "192.168.1.10"


def test_parse_addr_host_port_pins_both():
    assert config.parse_addr("127.0.0.1:19789") == ("127.0.0.1", 19789)


def test_parse_zone_table_mixes_bare_and_ported_hosts():
    table = config.parse_zone_table("1=192.168.1.10, 2=127.0.0.1:19789")
    assert table == {1: "192.168.1.10", 2: ("127.0.0.1", 19789)}


def test_zone_table_override_drives_backhaul_routing():
    from node.backhaul.batman_backhaul import BatmanBackhaul
    from node.backhaul.dynamic_zone_table import DynamicZoneTable

    # MESHLINK_ZONE_TABLE now seeds the dynamic table as operator-pinned
    # entries rather than being the whole static table (Phase 7).
    seed = config.parse_zone_table("2=127.0.0.1:1")
    b = BatmanBackhaul(
        zone_id=1,
        table=DynamicZoneTable(own_zone_id=1, seed=seed),
        bind=("127.0.0.1", 0),
    )
    try:
        # Zone 2 resolves to the override endpoint, not 10.77.0.2.
        assert b._table.addr_for(2) == ("127.0.0.1", 1)
    finally:
        b.stop()


def test_env_overrides_flow_into_config():
    # config reads env at import, so exercise it in a fresh interpreter.
    script = (
        "from node import config; "
        "print(config.BACKHAUL_ZONE_TABLE); "
        "print(config.BACKHAUL_BROADCAST_ADDR); "
        "print(config.BACKHAUL_UDP_PORT)"
    )
    out = subprocess.run(
        ["python3", "-c", script],
        env={
            "MESHLINK_ZONE_TABLE": "1=192.168.1.10,2=192.168.1.11:19789",
            "MESHLINK_BACKHAUL_BROADCAST_ADDR": "192.168.1.255",
            "MESHLINK_BACKHAUL_PORT": "19790",
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    table_line, broadcast_line, port_line = out.stdout.strip().splitlines()
    assert table_line == "{1: '192.168.1.10', 2: ('192.168.1.11', 19789)}"
    assert broadcast_line == "192.168.1.255"
    assert port_line == "19790"


def test_no_env_leaves_batman_adv_defaults():
    # Unset overrides → the production 10.77.0.x scheme, table default None.
    assert config.BACKHAUL_ZONE_TABLE is None
    assert config.BACKHAUL_BROADCAST_ADDR == "10.77.0.255"
