"""hostapd.conf templating (Phase 6, pure-Python side of setup_hostapd.sh)."""
import pytest

from node.wifi_ap.deployment_config import WifiDeploymentConfig
from node.wifi_ap.hostapd_config import render_hostapd_conf

CONFIG = WifiDeploymentConfig(ssid="MeshLink-Network", passphrase="venue-secret-2026")


def test_rendered_conf_carries_deployment_identity():
    conf = render_hostapd_conf(CONFIG, iface="wlan0", channel=6)
    lines = conf.splitlines()
    assert "interface=wlan0" in lines
    assert "ssid=MeshLink-Network" in lines
    assert "wpa_passphrase=venue-secret-2026" in lines
    assert "channel=6" in lines


def test_wpa2_psk_and_roaming_assists_present():
    lines = render_hostapd_conf(CONFIG).splitlines()
    assert "wpa=2" in lines
    assert "wpa_key_mgmt=WPA-PSK" in lines
    assert "rsn_pairwise=CCMP" in lines
    # 802.11k/v roaming assists (Add-On §2.4); 802.11r deliberately absent.
    assert "rrm_neighbor_report=1" in lines
    assert "bss_transition=1" in lines
    assert not any(line.startswith("ieee80211r") for line in lines)


@pytest.mark.parametrize("channel", [0, 14, 149])
def test_non_24ghz_channel_rejected(channel):
    # 5 GHz is the backhaul radio's band — the AP must never land there.
    with pytest.raises(ValueError, match="channel"):
        render_hostapd_conf(CONFIG, channel=channel)


def test_verify_script_can_extract_ssid():
    # scripts/verify_ssid_consistency.sh greps '^ssid=' — exactly one line
    # may match, or the self-check compares garbage.
    conf = render_hostapd_conf(CONFIG)
    assert sum(line.startswith("ssid=") for line in conf.splitlines()) == 1
    assert sum(line.startswith("wpa_passphrase=") for line in conf.splitlines()) == 1
