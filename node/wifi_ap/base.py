"""Platform-agnostic phone-facing AP provisioning (Phase 6).

hostapd (Raspberry Pi) and macOS Internet Sharing are two ways to do the one
job the WiFi Mesh Add-On asks for: put a radio into AP mode broadcasting the
deployment-wide SSID so phones join the mesh over WiFi. This base pins the
contract main.py drives; node/wifi_ap/__init__.py picks the backend by
platform exactly like node/ble picks a GATT backend (bluez vs corebluetooth).

Provisioning is best-effort and never fatal: Phase 6 is strictly additive, so
a machine that can't bring up an AP — no privileges, an unsupported OS, or a
Pi that provisions the AP out-of-band via scripts/setup_hostapd.sh — must
fall back to BLE-plus-listener behaviour, never crash the node.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from node.wifi_ap.deployment_config import WifiDeploymentConfig

log = logging.getLogger("meshlink.wifi.ap")


class ApProvisioner(ABC):
    """Brings the phone-facing radio up as an AP for one deployment identity."""

    def __init__(self, config: WifiDeploymentConfig) -> None:
        self.config = config

    @abstractmethod
    def start(self) -> bool:
        """Best-effort bring-up.

        Return True only if the AP is (believed to be) broadcasting the
        deployment SSID; return False when it degraded — always after logging
        the reason. Must not raise for environmental reasons: a node without
        an AP still serves BLE and the WiFi listener.
        """

    @abstractmethod
    def stop(self) -> None:
        """Tear down anything start() brought up. Idempotent."""

    @abstractmethod
    def configured_ssid(self) -> Optional[str]:
        """The SSID this machine is *configured* to broadcast right now, read
        back from the live system config (hostapd.conf / Internet Sharing
        plist), or None if no AP is configured. The software-side half of the
        SSID-consistency self-check."""

    def verify_consistency(self) -> bool:
        """Deployment config vs. live system config.

        The scripts/verify_ssid_consistency.sh three-way check adds an on-air
        leg; a single-radio AP can't reliably scan for itself (the radio is
        busy hosting), so on-air verification is phone-side. This still
        catches the common drift — a node configured for the wrong SSID,
        which would make it an isolated WiFi island.
        """
        want = self.config.ssid
        have = self.configured_ssid()
        if have is None:
            log.warning("SSID self-check: no AP configured on this node "
                        "(deployment SSID is %r)", want)
            return False
        if have != want:
            log.error("SSID self-check FAILED: broadcasting %r but deployment "
                      "config says %r — node is an isolated WiFi island",
                      have, want)
            return False
        log.info("SSID self-check OK: broadcasting deployment SSID %r", want)
        return True
