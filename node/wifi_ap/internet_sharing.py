"""macOS Internet Sharing AP provisioner — dev/test parity backend.

The counterpart to node/ble/corebluetooth.py: it lets a developer run a full
node on a Mac and have a phone actually join the phone-facing WiFi and reach
node/transport/wifi_transport.py, without a Pi. macOS has no hostapd, so the
only way to broadcast a WPA2 SSID from the built-in Wi-Fi card is Internet
Sharing, configured through SystemConfiguration's com.apple.nat.plist and run
by the com.apple.NetworkSharing launchd job.

Honest limitations (why this is a dev backend, not a deployment target):
- Enabling Internet Sharing needs root and takes the Wi-Fi card over as an
  AP, dropping any Wi-Fi the Mac was joined to. It is disruptive on purpose.
- Apple gates this behind a GUI toggle and the plist schema drifts between
  macOS releases, so start() is best-effort: it writes the SSID/passphrase so
  a manual toggle uses the right identity, tries to kick the job, and prints
  precise fallback instructions rather than pretend it always works.
- A radio hosting an AP can't reliably scan for its own SSID, so the on-air
  leg of the self-check is phone-side; verify_consistency() compares the
  deployment config against what we wrote into the plist.

The pure render/parse helpers (build_nat_airport_dict / ssid_from_nat_plist)
carry the identity and are unit-tested; the side-effecting start()/stop() are
exercised by hand (they need sudo and disrupt networking).
"""
import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from node.wifi_ap.base import ApProvisioner
from node.wifi_ap.deployment_config import WifiDeploymentConfig
# Reuse the 2.4 GHz default + MESHLINK_AP_CHANNEL override so the AP band
# story ("never share the backhaul's 5 GHz band") lives in one place.
from node.wifi_ap.hostapd_config import AP_CHANNEL

log = logging.getLogger("meshlink.wifi.ap")

# macOS Wi-Fi interface (en0 on most Macs; en1 on some). hostapd's wlan0
# default doesn't apply here.
AP_IFACE = os.environ.get("MESHLINK_AP_IFACE", "en0")

NAT_PLIST = Path("/Library/Preferences/SystemConfiguration/com.apple.nat.plist")
NAT_PLIST_BACKUP = NAT_PLIST.with_suffix(".plist.meshlink-bak")
SHARING_JOB = "system/com.apple.NetworkSharing"


def build_nat_airport_dict(config: WifiDeploymentConfig,
                           channel: int = AP_CHANNEL) -> dict:
    """The com.apple.nat.plist NAT>AirPort dict that hosts our AP.

    Same band rule as hostapd (2.4 GHz only; 5 GHz belongs to the backhaul).
    WPADisabled=0 + 40BitWEP=0 selects WPA2 personal; NetworkPassword is the
    deployment passphrase (already validated to WPA2's 8-63 printable ASCII by
    deployment_config).
    """
    if not 1 <= channel <= 13:
        raise ValueError(f"AP channel must be 1-13 (2.4 GHz), got {channel}")
    return {
        "40BitWEP": 0,
        "Channel": channel,
        "Enabled": 1,
        "NetworkName": config.ssid,
        "NetworkPassword": config.passphrase,
        "WPADisabled": 0,
    }


def ssid_from_nat_plist(plist: dict) -> Optional[str]:
    """Read back the configured hosted SSID (inverse of build_nat_airport_dict)."""
    airport = plist.get("NAT", {}).get("AirPort", {}) if plist else {}
    return airport.get("NetworkName")


class InternetSharingProvisioner(ApProvisioner):
    def start(self) -> bool:
        if sys.platform != "darwin":
            log.warning("Internet Sharing backend is macOS-only — AP not "
                        "provisioned")
            return False
        if os.geteuid() != 0:
            log.warning(
                "Internet Sharing needs root to configure. Run the node with "
                "sudo, or enable it by hand: System Settings > General > "
                "Sharing > Internet Sharing, 'Share your connection to: "
                "Wi-Fi', Wi-Fi Options SSID=%r with your deployment WPA2 "
                "passphrase. The WiFi listener + BLE still run.",
                self.config.ssid)
            return False
        try:
            self._write_sharing_config()
        except OSError as exc:
            log.warning("could not write %s (%s) — AP not provisioned",
                        NAT_PLIST, exc)
            return False
        if not self._enable_sharing():
            log.info("Internet Sharing not confirmed running — the SSID/"
                     "passphrase are configured, so toggling Internet Sharing "
                     "on in System Settings will use them.")
            return False
        return self.verify_consistency()

    def stop(self) -> None:
        if sys.platform != "darwin" or os.geteuid() != 0:
            return
        # Disable the hosted AP so the Mac's Wi-Fi is usable again; restore the
        # user's prior nat.plist if we backed one up.
        subprocess.run(["launchctl", "bootout", SHARING_JOB],
                       check=False, capture_output=True)
        if NAT_PLIST_BACKUP.exists():
            try:
                shutil.move(str(NAT_PLIST_BACKUP), str(NAT_PLIST))
            except OSError:
                log.warning("could not restore %s from backup", NAT_PLIST)

    def configured_ssid(self) -> Optional[str]:
        try:
            with NAT_PLIST.open("rb") as fh:
                plist = plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException):
            return None
        return ssid_from_nat_plist(plist)

    # -- internal ------------------------------------------------------------

    def _write_sharing_config(self) -> None:
        """Merge our hosted-AP settings into com.apple.nat.plist.

        Read-modify-write so we don't clobber an existing sharing setup, and
        back the original up once for stop() to restore.
        """
        try:
            with NAT_PLIST.open("rb") as fh:
                plist = plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException):
            plist = {}
        if NAT_PLIST.exists() and not NAT_PLIST_BACKUP.exists():
            shutil.copy2(str(NAT_PLIST), str(NAT_PLIST_BACKUP))

        nat = plist.setdefault("NAT", {})
        nat["AirPort"] = build_nat_airport_dict(self.config)
        nat["Enabled"] = 1

        tmp = NAT_PLIST.with_suffix(".plist.tmp")
        with tmp.open("wb") as fh:
            plistlib.dump(plist, fh)
        os.replace(tmp, NAT_PLIST)

    def _enable_sharing(self) -> bool:
        """Kick the NetworkSharing launchd job and report whether it's up."""
        subprocess.run(["launchctl", "enable", SHARING_JOB],
                       check=False, capture_output=True)
        subprocess.run(["launchctl", "kickstart", "-k", SHARING_JOB],
                       check=False, capture_output=True)
        probe = subprocess.run(["launchctl", "print", SHARING_JOB],
                               check=False, capture_output=True)
        return probe.returncode == 0
