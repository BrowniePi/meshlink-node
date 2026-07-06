"""Deployment-wide WiFi AP identity (Phase 6).

Every node in a venue must broadcast the exact same SSID and WPA2 passphrase
— that is what makes dozens of physical AP radios appear to phones as one
network (a standard ESS). A node whose values drift silently becomes an
isolated WiFi island, so the values are never set by hand on a Pi: they come
from one config file pushed unchanged to every node in the deployment (see
docs/wifi-ap-deployment.md), and scripts/verify_ssid_consistency.sh checks
the node is actually broadcasting them at boot.

File format is flat key=value lines ("#" comments allowed):

    ssid=MeshLink-Network
    passphrase=<deployment-wide WPA2 passphrase>
"""
import os
from dataclasses import dataclass
from pathlib import Path

# One canonical location on every node, so fleet push tooling and the boot
# self-check never disagree about where the truth lives. The env override
# serves tests and dev machines without /etc access.
DEPLOYMENT_CONF = Path(
    os.environ.get("MESHLINK_WIFI_DEPLOYMENT_CONF",
                   "/etc/meshlink/wifi_deployment.conf")
)


@dataclass(frozen=True)
class WifiDeploymentConfig:
    ssid: str
    passphrase: str


def parse_deployment_conf(text: str) -> WifiDeploymentConfig:
    """Parse and validate deployment config text.

    Raises ValueError on anything questionable — a node refusing to start its
    AP with a loud error beats one quietly broadcasting a wrong network.
    """
    values: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"line {lineno}: expected key=value, got {line!r}")
        key = key.strip()
        if key not in ("ssid", "passphrase"):
            # Unknown keys are rejected rather than ignored: a typo like
            # "pasphrase=" must not silently leave a node on defaults.
            raise ValueError(f"line {lineno}: unknown key {key!r}")
        if key in values:
            raise ValueError(f"line {lineno}: duplicate key {key!r}")
        values[key] = value.strip()

    for required in ("ssid", "passphrase"):
        if required not in values:
            raise ValueError(f"missing required key {required!r}")

    ssid, passphrase = values["ssid"], values["passphrase"]
    if not 1 <= len(ssid.encode()) <= 32:  # 802.11 SSID limit
        raise ValueError(f"ssid must be 1-32 bytes, got {len(ssid.encode())}")
    # hostapd.conf is line-oriented; control characters could smuggle in
    # extra directives (or just break parsing).
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ssid):
        raise ValueError("ssid contains control characters")
    # WPA2-PSK spec: 8-63 printable ASCII characters.
    if not 8 <= len(passphrase) <= 63:
        raise ValueError(f"passphrase must be 8-63 chars, got {len(passphrase)}")
    if not all(0x20 <= ord(c) <= 0x7E for c in passphrase):
        raise ValueError("passphrase must be printable ASCII")

    return WifiDeploymentConfig(ssid=ssid, passphrase=passphrase)


def load_deployment_config(path: Path = DEPLOYMENT_CONF) -> WifiDeploymentConfig:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ValueError(
            f"cannot read WiFi deployment config {path}: {exc} — "
            "push it from the central deployment config first "
            "(see docs/wifi-ap-deployment.md)"
        ) from exc
    return parse_deployment_conf(text)


if __name__ == "__main__":
    # Prints the validated SSID only (never the passphrase — this output ends
    # up in boot logs). Used by scripts/verify_ssid_consistency.sh.
    print(load_deployment_config().ssid)
