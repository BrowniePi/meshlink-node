"""WiFi access layer (Phase 6) — AP mode on the phone-facing radio.

Strictly additive per the WiFi Mesh Add-On design rule: nothing in the relay
pipeline, BLE serving, or backhaul depends on this package. Deployment config
loading and hostapd.conf templating are the unit-testable Python side; the
AP itself is brought up by a platform backend behind create_ap_provisioner()
— hostapd on the Pi (scripts/setup_hostapd.sh), Internet Sharing on a macOS
dev machine — chosen exactly like node/ble picks a GATT backend.
"""
import os
import sys


def create_ap_provisioner(config):
    """Instantiate the AP provisioner backend for this platform.

    Override with MESHLINK_AP_BACKEND=hostapd|internet_sharing. Backend
    imports are lazy so importing node.wifi_ap never pulls a backend's
    system dependencies on the wrong platform.
    """
    backend = os.environ.get("MESHLINK_AP_BACKEND")
    if not backend:
        backend = "internet_sharing" if sys.platform == "darwin" else "hostapd"
    if backend == "hostapd":
        from node.wifi_ap.hostapd import HostapdProvisioner
        return HostapdProvisioner(config)
    if backend == "internet_sharing":
        from node.wifi_ap.internet_sharing import InternetSharingProvisioner
        return InternetSharingProvisioner(config)
    raise ValueError(f"unknown MESHLINK_AP_BACKEND {backend!r}")
