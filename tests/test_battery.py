"""Battery parsing: pmset output (Mac) and power-supply sysfs (Pi/Linux)."""
from node.monitoring.battery import parse_pmset, parse_sysfs, read_battery

PMSET_DISCHARGING = """\
Now drawing from 'Battery Power'
 -InternalBattery-0 (id=12648547)\t87%; discharging; 4:32 remaining present: true
"""

PMSET_CHARGING = """\
Now drawing from 'AC Power'
 -InternalBattery-0 (id=12648547)\t42%; charging; 1:10 remaining present: true
"""

PMSET_NO_BATTERY = "Now drawing from 'AC Power'\n"


def test_pmset_discharging():
    assert parse_pmset(PMSET_DISCHARGING) == {
        "percent": 87, "charging": False, "source": "pmset",
    }


def test_pmset_charging():
    assert parse_pmset(PMSET_CHARGING) == {
        "percent": 42, "charging": True, "source": "pmset",
    }


def test_pmset_no_battery():
    assert parse_pmset(PMSET_NO_BATTERY) is None


def make_supply(root, name, type_="Battery", capacity="76", status="Discharging"):
    d = root / name
    d.mkdir(parents=True)
    (d / "type").write_text(f"{type_}\n")
    (d / "capacity").write_text(f"{capacity}\n")
    (d / "status").write_text(f"{status}\n")
    return d


def test_sysfs_battery(tmp_path):
    make_supply(tmp_path, "BAT0")
    assert parse_sysfs(tmp_path) == {
        "percent": 76, "charging": False, "source": "sysfs:BAT0",
    }


def test_sysfs_skips_mains_supply(tmp_path):
    ac = tmp_path / "AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")
    make_supply(tmp_path, "BAT0", status="Charging", capacity="100")
    assert parse_sysfs(tmp_path) == {
        "percent": 100, "charging": True, "source": "sysfs:BAT0",
    }


def test_sysfs_no_battery(tmp_path):
    assert parse_sysfs(tmp_path) is None


def test_sysfs_missing_root(tmp_path):
    assert parse_sysfs(tmp_path / "nope") is None


def test_read_battery_never_raises():
    # whatever this host is, the top-level reader must return dict or None
    result = read_battery()
    assert result is None or set(result) == {"percent", "charging", "source"}
