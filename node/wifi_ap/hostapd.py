"""Linux / Raspberry Pi AP provisioner — drives scripts/setup_hostapd.sh.

The heavy lifting (hostapd + dnsmasq, onboard radio into AP mode) already
lives in that idempotent script; this backend is the thin ApProvisioner
adapter so main.py can bring the AP up through the same contract as the macOS
backend. On the Pi the AP is normally provisioned out-of-band at boot (the
script run by systemd — see docs/wifi-ap-deployment.md), so main.py's default
(config.WIFI_AP_PROVISION="auto") leaves this alone there; the backend exists
for an explicit MESHLINK_AP_PROVISION=on and for the SSID self-check.
"""
import logging
import subprocess
from pathlib import Path
from typing import Optional

from node.wifi_ap.base import ApProvisioner

log = logging.getLogger("meshlink.wifi.ap")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup_hostapd.sh"
# Written by setup_hostapd.sh; the live source of the broadcast SSID.
HOSTAPD_CONF = Path("/etc/hostapd/meshlink.conf")


class HostapdProvisioner(ApProvisioner):
    def start(self) -> bool:
        if not SETUP_SCRIPT.exists():
            log.warning("setup script %s missing — AP not provisioned",
                        SETUP_SCRIPT)
            return False
        try:
            # setup_hostapd.sh renders hostapd.conf from the deployment config
            # and (re)starts hostapd/dnsmasq. It insists on root itself.
            subprocess.run(["sudo", str(SETUP_SCRIPT)], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            log.warning("setup_hostapd.sh failed (%s) — AP not provisioned; "
                        "node serves BLE + WiFi listener only", exc)
            return False
        return self.verify_consistency()

    def stop(self) -> None:
        # The AP is a system service (hostapd/dnsmasq via systemd) shared by
        # every phone at the venue; a single node process exiting must not
        # tear it down under the others. Teardown is an ops action, not ours.
        pass

    def configured_ssid(self) -> Optional[str]:
        try:
            for line in HOSTAPD_CONF.read_text().splitlines():
                if line.startswith("ssid="):
                    return line[len("ssid="):]
        except OSError:
            return None
        return None
