"""Backhaul radio runtime checks (Phase 3).

The heavy lifting — putting the second radio into 802.11s mesh mode — is
system configuration and lives in scripts/setup_backhaul_radio.sh. This
module is the runtime side: the node can ask "is my backhaul radio actually
up and in mesh mode?" at startup and log a clear diagnosis instead of
failing obscurely later when batman-adv has no link underneath it.

Parsing is separated from invocation so the parsing is unit-testable
without `iw` (which only exists on Linux).
"""
import logging
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger("meshlink.backhaul")

# Must match scripts/setup_backhaul_radio.sh defaults.
BACKHAUL_IFACE = "wlan1"
MESH_ID = "meshlink-backhaul"
MESH_FREQ_MHZ = 5745  # channel 149, 5 GHz — clear of the phone-facing radio


@dataclass
class RadioStatus:
    iface: str
    exists: bool
    is_mesh_mode: bool = False
    mesh_id: str | None = None
    freq_mhz: int | None = None

    @property
    def ready(self) -> bool:
        return (
            self.exists
            and self.is_mesh_mode
            and self.mesh_id == MESH_ID
            and self.freq_mhz == MESH_FREQ_MHZ
        )


def parse_iw_info(iface: str, iw_output: str) -> RadioStatus:
    """Parse `iw dev <iface> info` output into a RadioStatus."""
    type_match = re.search(r"^\s*type (\S[^\n]*)$", iw_output, re.MULTILINE)
    meshid_match = re.search(r"^\s*meshid (\S+)", iw_output, re.MULTILINE)
    freq_match = re.search(r"\((\d+)(?:\.\d+)? MHz\)", iw_output)
    return RadioStatus(
        iface=iface,
        exists=True,
        is_mesh_mode=bool(type_match) and type_match.group(1).strip() == "mesh point",
        mesh_id=meshid_match.group(1) if meshid_match else None,
        freq_mhz=int(freq_match.group(1)) if freq_match else None,
    )


def radio_status(iface: str = BACKHAUL_IFACE) -> RadioStatus:
    """Query the backhaul radio via `iw`. Never raises — absent radio,
    missing `iw`, or a non-wireless interface all come back as not-ready."""
    try:
        out = subprocess.run(
            ["iw", "dev", iface, "info"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return RadioStatus(iface=iface, exists=False)
    return parse_iw_info(iface, out)


def check_backhaul_radio(iface: str = BACKHAUL_IFACE) -> bool:
    """Log a diagnosis of the backhaul radio; True if it is mesh-ready."""
    status = radio_status(iface)
    if status.ready:
        log.info("backhaul radio %s in 802.11s mesh '%s' on %d MHz",
                 iface, status.mesh_id, status.freq_mhz)
    elif not status.exists:
        log.warning("backhaul radio %s not found — run scripts/setup_backhaul_radio.sh "
                    "(node will serve BLE only, no node-to-node backhaul)", iface)
    else:
        log.warning("backhaul radio %s present but not mesh-ready "
                    "(mode ok=%s, meshid=%s, freq=%s) — "
                    "run scripts/setup_backhaul_radio.sh",
                    iface, status.is_mesh_mode, status.mesh_id, status.freq_mhz)
    return status.ready
