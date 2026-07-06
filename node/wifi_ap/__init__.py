"""WiFi access layer (Phase 6) — hostapd AP mode on the phone-facing radio.

Strictly additive per the WiFi Mesh Add-On design rule: nothing in the relay
pipeline, BLE serving, or backhaul depends on this package. The heavy lifting
(putting the radio into AP mode) is system configuration in
scripts/setup_hostapd.sh; these modules are the unit-testable Python side —
deployment config loading and hostapd.conf templating.
"""
