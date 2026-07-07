"""Host health for heartbeats: platform, CPU temp, load, memory, disk.

Everything here is best-effort — a field that can't be read on this
platform is null, never an exception. CPU temperature and memory come from
Linux interfaces (the Pi is the deployment target); Macs report null there
and the dashboard just leaves the cell blank on dev nodes.
"""
import logging
import os
import platform
import shutil
from pathlib import Path

log = logging.getLogger("meshlink.system_stats")

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
MEMINFO = Path("/proc/meminfo")


def read_system_stats() -> dict:
    return {
        "platform": platform.platform(),
        "cpu_temp_c": _cpu_temp_c(),
        "load_avg_1m": _load_avg_1m(),
        "mem_used_percent": _mem_used_percent(),
        "disk_used_percent": _disk_used_percent(),
    }


def _cpu_temp_c(path: Path = THERMAL_ZONE) -> float | None:
    try:
        return round(int(path.read_text().strip()) / 1000, 1)  # millidegrees
    except (OSError, ValueError):
        return None


def _load_avg_1m() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def _mem_used_percent(path: Path = MEMINFO) -> float | None:
    try:
        fields = {}
        for line in path.read_text().splitlines():
            key, _, rest = line.partition(":")
            fields[key] = int(rest.split()[0])  # values are in kB
        total, available = fields["MemTotal"], fields["MemAvailable"]
        return round(100 * (total - available) / total, 1)
    except (OSError, KeyError, IndexError, ValueError, ZeroDivisionError):
        return None


def _disk_used_percent(path: str = "/") -> float | None:
    try:
        usage = shutil.disk_usage(path)
        return round(100 * usage.used / usage.total, 1)
    except (OSError, ZeroDivisionError):
        return None
