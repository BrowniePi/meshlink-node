"""Dynamic zone → node-address table (Phase 7, replaces static_zone_table.py).

Where Phase 3 hand-wired zone N ↔ 10.77.0.N, this table is filled at runtime
from the announcements nodes broadcast over the backhaul (see zone_sync.py):
each node periodically says "zone Z is reachable at addr A", and every other
node records it here. Nodes that stop announcing age out after entry_ttl_s, so
a node leaving the mesh (or a phone-driven zone reassignment) is reflected
without redeploying config to anyone else.

Thread-safe: announcements arrive on the backhaul receive thread while the
relay reads the table on its own thread(s).

Fallback contract is unchanged from Phase 3: addr_for() returns None for a zone
not (yet) known locally, and callers flood all nodes rather than dropping
(Technical Reference §5.2).
"""
import threading
import time
from typing import Callable

# An address is "host" (default backhaul port) or an explicit ("host", port),
# matching batman_backhaul.Addr — kept as a local alias to avoid an import
# cycle (batman_backhaul imports this module).
Addr = str | tuple[str, int]

# A learned entry older than this (seconds, monotonic) is treated as gone. The
# node driving the table passes its own value derived from the announce
# interval; this default tolerates ~3 missed announcements at a 60 s interval.
DEFAULT_ENTRY_TTL_S = 180.0


class DynamicZoneTable:
    def __init__(
        self,
        own_zone_id: int,
        seed: dict[int, Addr] | None = None,
        entry_ttl_s: float = DEFAULT_ENTRY_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._own_zone_id = own_zone_id
        self._entry_ttl_s = entry_ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        # Operator-pinned entries from MESHLINK_ZONE_TABLE (dev nodes without a
        # batman-adv mesh to announce over): never expire, used as a fallback
        # when nothing has been learned for a zone.
        self._pinned: dict[int, Addr] = dict(seed) if seed else {}
        # Learned entries: zone_id -> (addr, last_seen_monotonic).
        self._learned: dict[int, tuple[Addr, float]] = {}

    def learn(self, zone_id: int, addr: Addr) -> None:
        """Record (or refresh) that zone_id is reachable at addr."""
        with self._lock:
            self._learned[zone_id] = (addr, self._clock())

    def addr_for(self, zone_id: int) -> Addr | None:
        """Current address for zone_id, or None if unknown/expired.

        A freshly-announced address wins over a pinned seed for the same zone
        (the live mesh is ground truth); the seed is only a fallback.
        """
        with self._lock:
            entry = self._learned.get(zone_id)
            if entry is not None and not self._expired(entry[1]):
                return entry[0]
            return self._pinned.get(zone_id)

    def forget_stale(self) -> list[int]:
        """Drop learned entries past their TTL. Returns the zones forgotten."""
        with self._lock:
            gone = [z for z, (_, seen) in self._learned.items()
                    if self._expired(seen)]
            for zone_id in gone:
                del self._learned[zone_id]
            return gone

    def known_zones(self) -> set[int]:
        """Zones currently routable (fresh learned entries plus pinned seed)."""
        with self._lock:
            zones = {z for z, (_, seen) in self._learned.items()
                     if not self._expired(seen)}
            zones.update(self._pinned)
            return zones

    def peer_count(self) -> int:
        """Known zones other than our own — reported as batman_peer_count."""
        return len(self.known_zones() - {self._own_zone_id})

    def snapshot(self) -> dict[int, Addr]:
        """{zone_id: addr} of everything currently routable (for logging)."""
        with self._lock:
            table = dict(self._pinned)
            table.update(
                {z: addr for z, (addr, seen) in self._learned.items()
                 if not self._expired(seen)}
            )
            return table

    def _expired(self, last_seen: float) -> bool:
        return self._clock() - last_seen > self._entry_ttl_s
