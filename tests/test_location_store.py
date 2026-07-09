"""Retention invariant 3: the store holds exactly one (the latest) coordinate
per identity — overwrite, never append."""
from node.location.store import LocationStore

IDENTITY = b"\x01" * 32
OTHER = b"\x02" * 32


def test_overwrite_not_append_100_beacons():
    """Fire 100 beacons for one identity: only the latest survives, and the
    store never holds more than one row for it at any point."""
    store = LocationStore(clock=lambda: 1000.0)
    for i in range(100):
        store.update(IDENTITY, lat_microdeg=51000000 + i, lon_microdeg=-127000 - i,
                     accuracy_m=5, zone_id=3)
        assert store.row_count(IDENTITY) == 1  # after every single beacon

    row = store.get(IDENTITY)
    assert row.lat_microdeg == 51000099        # the 100th beacon, nothing older
    assert row.lon_microdeg == -127099


def test_one_row_per_identity_independent():
    store = LocationStore(clock=lambda: 1000.0)
    store.update(IDENTITY, lat_microdeg=1, lon_microdeg=2, accuracy_m=3, zone_id=1)
    store.update(OTHER, lat_microdeg=4, lon_microdeg=5, accuracy_m=6, zone_id=2)
    assert store.get(IDENTITY).lat_microdeg == 1
    assert store.get(OTHER).lat_microdeg == 4
    assert store.row_count(IDENTITY) == 1
    assert store.row_count(OTHER) == 1


def test_no_history_container_exists():
    """A movement trail has nowhere to live: the per-identity value is a flat
    dataclass of scalars, not a list/deque/dict of positions."""
    store = LocationStore(clock=lambda: 1000.0)
    store.update(IDENTITY, lat_microdeg=1, lon_microdeg=2, accuracy_m=3, zone_id=1)
    row = store.get(IDENTITY)
    assert all(isinstance(v, (int, float)) for v in vars(row).values()), vars(row)


def test_beacon_age_and_unknown_and_forget():
    now = [1000.0]
    store = LocationStore(clock=lambda: now[0])
    assert store.get(IDENTITY) is None
    store.update(IDENTITY, lat_microdeg=1, lon_microdeg=2, accuracy_m=3, zone_id=1)
    now[0] = 1040.0
    assert store.beacon_age_s(store.get(IDENTITY)) == 40
    store.forget(IDENTITY)
    assert store.get(IDENTITY) is None
    assert store.row_count(IDENTITY) == 0
