"""Battery status for heartbeats — Mac (pmset) and Pi/Linux (sysfs).

Dev nodes are MacBooks (real battery via `pmset -g batt`); deployed Pis
read the standard Linux power-supply sysfs, which is what any UPS/battery
HAT driver exposes. A node with no battery at all (bench Pi on wall power)
reports null — the dashboard should treat that as "mains powered", not 0%.

Never raises: battery is monitoring garnish, a parse failure must not cost
a heartbeat.
"""
import logging
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("meshlink.battery")

SYSFS_POWER_SUPPLY = Path("/sys/class/power_supply")

# pmset line: " -InternalBattery-0 (id=1234)	87%; discharging; 4:32 remaining"
_PMSET_RE = re.compile(r"(\d{1,3})%;\s*([\w ]+?)[;\n]")


def read_battery() -> dict | None:
    """Best-effort battery snapshot: {"percent", "charging", "source"}.

    Returns None when the platform has no battery (or reading failed) —
    callers put that null in the heartbeat as-is.
    """
    try:
        if sys.platform == "darwin":
            return _read_pmset()
        return _read_sysfs()
    except Exception as exc:  # noqa: BLE001 — never cost a heartbeat
        log.warning("battery read failed: %s", exc)
        return None


def _read_pmset() -> dict | None:
    out = subprocess.run(
        ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3,
    ).stdout
    return parse_pmset(out)


def parse_pmset(out: str) -> dict | None:
    match = _PMSET_RE.search(out)
    if not match:
        return None  # Mac with no battery (e.g. a Mac mini)
    percent, state = int(match.group(1)), match.group(2).strip().lower()
    # pmset states: charging / discharging / charged / finishing charge /
    # AC attached (plugged in, not charging past its limit).
    return {
        "percent": percent,
        "charging": state not in ("discharging",),
        "source": "pmset",
    }


def _read_sysfs(root: Path = SYSFS_POWER_SUPPLY) -> dict | None:
    return parse_sysfs(root)


def parse_sysfs(root: Path) -> dict | None:
    """First sysfs supply of type Battery wins (Pis have at most one HAT)."""
    if not root.is_dir():
        return None
    for supply in sorted(root.iterdir()):
        try:
            if (supply / "type").read_text().strip() != "Battery":
                continue
            percent = int((supply / "capacity").read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            status = (supply / "status").read_text().strip().lower()
        except OSError:
            status = "unknown"
        return {
            "percent": percent,
            # sysfs statuses: Charging / Discharging / Full / Not charging /
            # Unknown. Only an explicit Discharging means "on battery".
            "charging": status != "discharging",
            "source": f"sysfs:{supply.name}",
        }
    return None
