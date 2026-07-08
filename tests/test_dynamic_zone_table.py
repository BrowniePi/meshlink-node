"""DynamicZoneTable: learn, expire, seed fallback, and the peer count."""
from node.backhaul.dynamic_zone_table import DynamicZoneTable


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_learn_then_resolve():
    table = DynamicZoneTable(own_zone_id=1)
    table.learn(2, "10.77.0.2")
    assert table.addr_for(2) == "10.77.0.2"


def test_unknown_zone_returns_none():
    table = DynamicZoneTable(own_zone_id=1)
    assert table.addr_for(9) is None


def test_learned_entry_expires_after_ttl():
    clock = FakeClock()
    table = DynamicZoneTable(own_zone_id=1, entry_ttl_s=180, clock=clock)
    table.learn(2, "10.77.0.2")

    clock.advance(179)
    assert table.addr_for(2) == "10.77.0.2"  # still fresh
    clock.advance(2)                          # now 181 s old, past ttl
    assert table.addr_for(2) is None


def test_refresh_keeps_entry_alive():
    clock = FakeClock()
    table = DynamicZoneTable(own_zone_id=1, entry_ttl_s=180, clock=clock)
    table.learn(2, "10.77.0.2")
    clock.advance(170)
    table.learn(2, "10.77.0.2")  # re-announced before expiry
    clock.advance(170)           # 170 since refresh, still fresh
    assert table.addr_for(2) == "10.77.0.2"


def test_forget_stale_reports_and_drops_expired_zones():
    clock = FakeClock()
    table = DynamicZoneTable(own_zone_id=1, entry_ttl_s=60, clock=clock)
    table.learn(2, "10.77.0.2")
    table.learn(3, "10.77.0.3")
    clock.advance(30)
    table.learn(3, "10.77.0.3")  # keep zone 3 warm
    clock.advance(40)            # zone 2 now 70 s old, zone 3 only 40 s

    assert table.forget_stale() == [2]
    assert table.known_zones() == {3}


def test_pinned_seed_never_expires_and_is_fallback():
    clock = FakeClock()
    table = DynamicZoneTable(own_zone_id=1, seed={2: "192.168.1.2"},
                             entry_ttl_s=60, clock=clock)
    clock.advance(10_000)
    assert table.addr_for(2) == "192.168.1.2"  # seed outlives any ttl


def test_learned_entry_wins_over_seed():
    table = DynamicZoneTable(own_zone_id=1, seed={2: "192.168.1.2"})
    table.learn(2, "10.77.0.99")
    assert table.addr_for(2) == "10.77.0.99"  # live mesh beats static seed


def test_peer_count_excludes_own_zone():
    table = DynamicZoneTable(own_zone_id=1)
    table.learn(1, "10.77.0.1")  # our own zone announced back to us
    table.learn(2, "10.77.0.2")
    table.learn(3, "10.77.0.3")
    assert table.peer_count() == 2  # zones 2 and 3, not our own zone 1


def test_snapshot_merges_seed_and_learned():
    table = DynamicZoneTable(own_zone_id=1, seed={5: "192.168.1.5"})
    table.learn(2, "10.77.0.2")
    assert table.snapshot() == {5: "192.168.1.5", 2: "10.77.0.2"}
