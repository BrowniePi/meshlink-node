"""Backhaul radio status parsing (Phase 3, pure-Python side of setup_backhaul_radio.sh)."""
from node.backhaul.radio_config import (
    MESH_FREQ_MHZ,
    MESH_ID,
    RadioStatus,
    parse_iw_info,
    radio_status,
)

IW_MESH_OUTPUT = f"""\
Interface wlan1
	ifindex 4
	wdev 0x100000001
	addr dc:a6:32:aa:bb:cc
	type mesh point
	meshid {MESH_ID}
	wiphy 1
	channel 149 (5745 MHz), width: 20 MHz, center1: 5745 MHz
	txpower 20.00 dBm
"""

IW_MANAGED_OUTPUT = """\
Interface wlan1
	ifindex 4
	wdev 0x100000001
	addr dc:a6:32:aa:bb:cc
	type managed
	wiphy 1
	channel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz
"""


def test_parse_mesh_mode_radio_is_ready():
    status = parse_iw_info("wlan1", IW_MESH_OUTPUT)
    assert status.is_mesh_mode
    assert status.mesh_id == MESH_ID
    assert status.freq_mhz == MESH_FREQ_MHZ == 5745
    assert status.ready


def test_parse_managed_mode_radio_is_not_ready():
    status = parse_iw_info("wlan1", IW_MANAGED_OUTPUT)
    assert not status.is_mesh_mode
    assert status.mesh_id is None
    assert status.freq_mhz == 2437
    assert not status.ready


def test_wrong_mesh_id_is_not_ready():
    status = parse_iw_info("wlan1", IW_MESH_OUTPUT.replace(MESH_ID, "someone-elses-mesh"))
    assert status.is_mesh_mode and not status.ready


def test_missing_radio_reports_not_ready_without_raising():
    status = radio_status("definitely-not-a-radio0")
    assert status == RadioStatus(iface="definitely-not-a-radio0", exists=False)
    assert not status.ready
