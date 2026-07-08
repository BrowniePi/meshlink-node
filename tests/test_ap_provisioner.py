"""AP provisioner abstraction + backends (Phase 6, macOS dev-parity port).

The platform-selected AP provisioner is the WiFi twin of node/ble's
create_gatt_server(): hostapd on the Pi, Internet Sharing on a Mac. These
cover the deterministic parts — backend selection and the pure config
render/parse — the same way test_hostapd_config.py covers hostapd templating.
The side-effecting bring-up (sudo, launchctl, dropping the Mac's Wi-Fi) is
exercised by hand.
"""
import plistlib

import pytest

from node.wifi_ap import create_ap_provisioner
from node.wifi_ap.base import ApProvisioner
from node.wifi_ap.deployment_config import WifiDeploymentConfig
from node.wifi_ap.hostapd import HostapdProvisioner
from node.wifi_ap.internet_sharing import (
    InternetSharingProvisioner,
    build_nat_airport_dict,
    ssid_from_nat_plist,
)

CONFIG = WifiDeploymentConfig(ssid="MeshLink-Network", passphrase="venue-secret-2026")


# -- factory selection (mirrors node/ble create_gatt_server) -----------------

def test_factory_picks_hostapd_on_linux(monkeypatch):
    monkeypatch.delenv("MESHLINK_AP_BACKEND", raising=False)
    monkeypatch.setattr("node.wifi_ap.sys.platform", "linux")
    assert isinstance(create_ap_provisioner(CONFIG), HostapdProvisioner)


def test_factory_picks_internet_sharing_on_macos(monkeypatch):
    monkeypatch.delenv("MESHLINK_AP_BACKEND", raising=False)
    monkeypatch.setattr("node.wifi_ap.sys.platform", "darwin")
    assert isinstance(create_ap_provisioner(CONFIG), InternetSharingProvisioner)


def test_factory_env_override_wins(monkeypatch):
    monkeypatch.setattr("node.wifi_ap.sys.platform", "linux")
    monkeypatch.setenv("MESHLINK_AP_BACKEND", "internet_sharing")
    assert isinstance(create_ap_provisioner(CONFIG), InternetSharingProvisioner)


def test_factory_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("MESHLINK_AP_BACKEND", "bogus")
    with pytest.raises(ValueError, match="bogus"):
        create_ap_provisioner(CONFIG)


# -- shared consistency check ------------------------------------------------

class _FakeProvisioner(ApProvisioner):
    def __init__(self, config, configured):
        super().__init__(config)
        self._configured = configured

    def start(self):  # pragma: no cover - not exercised here
        return True

    def stop(self):  # pragma: no cover
        pass

    def configured_ssid(self):
        return self._configured


def test_verify_consistency_ok_when_ssid_matches():
    assert _FakeProvisioner(CONFIG, "MeshLink-Network").verify_consistency()


def test_verify_consistency_fails_on_drift():
    assert not _FakeProvisioner(CONFIG, "SomeoneElses-WiFi").verify_consistency()


def test_verify_consistency_fails_when_no_ap_configured():
    assert not _FakeProvisioner(CONFIG, None).verify_consistency()


# -- macOS Internet Sharing plist render/parse -------------------------------

def test_nat_airport_dict_carries_identity():
    airport = build_nat_airport_dict(CONFIG, channel=6)
    assert airport["NetworkName"] == "MeshLink-Network"
    assert airport["NetworkPassword"] == "venue-secret-2026"
    assert airport["Channel"] == 6
    # WPA2 personal, not WEP.
    assert airport["WPADisabled"] == 0
    assert airport["40BitWEP"] == 0
    assert airport["Enabled"] == 1


@pytest.mark.parametrize("channel", [0, 14, 149])
def test_nat_airport_dict_rejects_non_24ghz_channel(channel):
    # Same band rule as hostapd — 5 GHz belongs to the backhaul.
    with pytest.raises(ValueError, match="channel"):
        build_nat_airport_dict(CONFIG, channel=channel)


def test_ssid_round_trips_through_nat_plist():
    # What _write_sharing_config lands in com.apple.nat.plist, read back by
    # configured_ssid()/the self-check.
    plist = {"NAT": {"AirPort": build_nat_airport_dict(CONFIG), "Enabled": 1}}
    blob = plistlib.dumps(plist)
    assert ssid_from_nat_plist(plistlib.loads(blob)) == "MeshLink-Network"


def test_ssid_from_nat_plist_none_when_unconfigured():
    assert ssid_from_nat_plist({}) is None
    assert ssid_from_nat_plist({"NAT": {}}) is None


# -- hostapd backend read-back -----------------------------------------------

def test_hostapd_configured_ssid_reads_conf(tmp_path, monkeypatch):
    conf = tmp_path / "meshlink.conf"
    conf.write_text("interface=wlan0\nssid=MeshLink-Network\nchannel=6\n")
    monkeypatch.setattr("node.wifi_ap.hostapd.HOSTAPD_CONF", conf)
    assert HostapdProvisioner(CONFIG).configured_ssid() == "MeshLink-Network"


def test_hostapd_configured_ssid_none_when_conf_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("node.wifi_ap.hostapd.HOSTAPD_CONF",
                        tmp_path / "nope.conf")
    assert HostapdProvisioner(CONFIG).configured_ssid() is None
