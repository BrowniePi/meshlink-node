"""Latest-coordinate-only location table (security invariant 3).

One row per stable identity, overwritten on every beacon — never appended.
There is no history buffer and no track log anywhere in this class: the row
value is a plain dict of scalars keyed by identity, so a compromised node
can leak at most a snapshot, never a movement trail. The retention test
fires 100 beacons and asserts exactly one row survives.

The mapping key is the stable identity (wire `sender_key`) taken from the
signed LOCATION beacon envelope, which arrives only over an established
BLE/WiFi session. It lives here, node-internal, on the backhaul side of the
boundary — phone-facing BLE advertising still carries only the 15-minute
rotating `ephemeral_id` (invariant 2, Technical Reference §7.4).
"""
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class LocationRow:
    lat_microdeg: int
    lon_microdeg: int
    accuracy_m: int
    zone_id: int
    last_beacon_ts: float


class LocationStore:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        # identity (32-byte sender_key) -> LocationRow. Plain assignment on
        # update is the enforcement of overwrite-not-append: the previous
        # coordinate is unreferenced the moment a new beacon lands.
        self._rows: dict[bytes, LocationRow] = {}

    def update(self, identity: bytes, *, lat_microdeg: int, lon_microdeg: int,
               accuracy_m: int, zone_id: int) -> None:
        row = LocationRow(lat_microdeg, lon_microdeg, accuracy_m, zone_id,
                          self._clock())
        with self._lock:
            self._rows[identity] = row

    def get(self, identity: bytes) -> Optional[LocationRow]:
        with self._lock:
            return self._rows.get(identity)

    def beacon_age_s(self, row: LocationRow) -> int:
        return max(0, int(self._clock() - row.last_beacon_ts))

    def row_count(self, identity: bytes) -> int:
        """Rows held for one identity — by construction 0 or 1; asserted in
        tests so the invariant survives refactors."""
        with self._lock:
            return 1 if identity in self._rows else 0

    def forget(self, identity: bytes) -> None:
        with self._lock:
            self._rows.pop(identity, None)
