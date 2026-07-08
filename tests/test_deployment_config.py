"""Deployment-wide WiFi config parsing (Phase 6, pure-Python side of task 2)."""
import pytest

from node.wifi_ap.deployment_config import (
    WifiDeploymentConfig,
    load_deployment_config,
    parse_deployment_conf,
)

VALID_CONF = """\
# MeshLink deployment-wide WiFi identity
ssid=MeshLink-Network
passphrase=venue-secret-2026
"""


def test_parse_valid_conf():
    config = parse_deployment_conf(VALID_CONF)
    assert config == WifiDeploymentConfig(
        ssid="MeshLink-Network", passphrase="venue-secret-2026"
    )


def test_comments_blank_lines_and_whitespace_ignored():
    config = parse_deployment_conf(
        "\n# comment\n  ssid = MeshLink-Network  \n\npassphrase= secret-12\n"
    )
    assert config.ssid == "MeshLink-Network"
    assert config.passphrase == "secret-12"


@pytest.mark.parametrize("broken,match", [
    ("ssid=MeshLink-Network\n", "missing required key 'passphrase'"),
    ("passphrase=secret-12\n", "missing required key 'ssid'"),
    ("ssid=A\npassphrase=secret-12\nssid=B\n", "duplicate key"),
    ("ssid=A\npasphrase=secret-12\n", "unknown key"),  # typo must not pass
    ("just a line\n", "expected key=value"),
    ("ssid=\npassphrase=secret-12\n", "ssid must be 1-32 bytes"),
    (f"ssid={'x' * 33}\npassphrase=secret-12\n", "ssid must be 1-32 bytes"),
    ("ssid=A\npassphrase=short\n", "passphrase must be 8-63 chars"),
    (f"ssid=A\npassphrase={'x' * 64}\n", "passphrase must be 8-63 chars"),
    ("ssid=A\npassphrase=ключ-не-ascii\n", "printable ASCII"),
])
def test_invalid_conf_rejected(broken, match):
    with pytest.raises(ValueError, match=match):
        parse_deployment_conf(broken)


def test_control_characters_in_ssid_rejected():
    # hostapd.conf is line-oriented; an embedded control char must not be
    # able to smuggle in extra directives.
    with pytest.raises(ValueError, match="control characters"):
        parse_deployment_conf("ssid=Mesh\tLink\npassphrase=secret-12\n")


def test_load_from_file(tmp_path):
    conf = tmp_path / "wifi_deployment.conf"
    conf.write_text(VALID_CONF)
    assert load_deployment_config(conf).ssid == "MeshLink-Network"


def test_missing_file_raises_with_deployment_pointer(tmp_path):
    with pytest.raises(ValueError, match="wifi-ap-deployment"):
        load_deployment_config(tmp_path / "nope.conf")
