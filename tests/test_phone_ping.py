"""Phone telemetry ping: wire codec, report lifecycle, relay demux,
heartbeat integration."""
import json

from node.monitoring.phone_ping import (
    PHONE_PING_MAGIC,
    PhonePingService,
    decode,
    encode_ping,
    encode_pong,
    is_telemetry_frame,
)
from node.relay import NodeRelay
from tests.helpers import FakeTransport, build_packet
from tests.test_relay import RecordingBackhaul


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_service(peers=("phoneA", "phoneB"), interval_s=120.0):
    clock = FakeClock()
    transport = FakeTransport(list(peers))
    service = PhonePingService(transport, interval_s=interval_s, clock=clock)
    return service, transport, clock


# -- wire codec ---------------------------------------------------------------

def test_encode_ping_matches_wire_contract():
    assert encode_ping() == b'MLPP1{"t":"ping"}'


def test_encode_pong_matches_spec_example():
    pong = encode_pong(lat=51.5074, lon=-0.1278, battery=84, charging=True)
    assert pong == (
        b'MLPP1{"t":"pong","lat":51.5074,"lon":-0.1278,'
        b'"battery":84,"charging":true}'
    )
    # charging omitted when unknown; other fields explicitly null
    assert encode_pong() == b'MLPP1{"t":"pong","lat":null,"lon":null,"battery":null}'


def test_is_telemetry_frame_matches_only_the_magic():
    assert is_telemetry_frame(encode_ping())
    assert is_telemetry_frame(PHONE_PING_MAGIC)
    assert not is_telemetry_frame(b"MLP")
    assert not is_telemetry_frame(build_packet(ttl=5, zone_id=1))


def test_decode_is_lenient():
    assert decode(encode_ping()) == {"t": "ping"}
    assert decode(PHONE_PING_MAGIC + b"not json") is None
    assert decode(PHONE_PING_MAGIC + b'{"x":1}') is None  # no "t"
    assert decode(PHONE_PING_MAGIC + b"[1,2]") is None  # not an object
    assert decode(b"XXXXX" + b'{"t":"ping"}') is None  # wrong magic
    # unknown extra keys pass through
    assert decode(PHONE_PING_MAGIC + b'{"t":"ping","v":2}') == {"t": "ping", "v": 2}


# -- ping fan-out -------------------------------------------------------------

def test_ping_all_sends_one_ping_to_every_connected_phone():
    service, transport, _ = make_service(["phoneA", "phoneB", "phoneC"])
    service.ping_all()
    assert transport.sent == [
        ("phoneA", encode_ping()),
        ("phoneB", encode_ping()),
        ("phoneC", encode_ping()),
    ]


def test_ping_all_survives_a_send_failure():
    service, transport, _ = make_service(["phoneA", "phoneB"])

    def failing_send(peer_id, data, _orig=transport.send):
        if peer_id == "phoneA":
            raise OSError("peer vanished")
        _orig(peer_id, data)

    transport.send = failing_send
    service.ping_all()
    assert [p for p, _ in transport.sent] == ["phoneB"]


# -- report lifecycle ---------------------------------------------------------

def test_pong_stored_as_latest_report_per_phone():
    service, _, clock = make_service()
    service.handle_frame("phoneA", encode_pong(lat=1.0, lon=2.0, battery=90))
    clock.now += 120
    service.handle_frame("phoneA", encode_pong(lat=1.1, lon=2.1, battery=88,
                                               charging=False))
    reports = service.reports()
    assert reports == [{
        "peer_id": "phoneA",
        "lat": 1.1,
        "lon": 2.1,
        "battery": 88,
        "charging": False,
        "age_s": 0,
    }]


def test_missing_or_mistyped_fields_stored_as_null():
    service, _, _ = make_service()
    frame = PHONE_PING_MAGIC + json.dumps(
        {"t": "pong", "lat": "oops", "battery": True}
    ).encode()
    service.handle_frame("phoneA", frame)
    (report,) = service.reports()
    assert report["lat"] is None
    assert report["lon"] is None
    assert report["battery"] is None
    assert report["charging"] is None


def test_non_pong_and_garbage_frames_are_dropped_silently():
    service, _, _ = make_service()
    service.handle_frame("phoneA", encode_ping())  # echoed ping
    service.handle_frame("phoneA", PHONE_PING_MAGIC + b"garbage")
    assert service.reports() == []


def test_reports_age_out_after_three_missed_pings():
    service, _, clock = make_service(interval_s=120.0)
    service.handle_frame("phoneA", encode_pong(battery=80))
    clock.now += 359  # just inside 3 × interval
    assert [r["peer_id"] for r in service.reports()] == ["phoneA"]
    assert service.reports()[0]["age_s"] == 359

    clock.now += 2  # now past the TTL
    assert service.reports() == []


# -- relay demux --------------------------------------------------------------

def make_relay(peers, phone_ping=None):
    transport = FakeTransport(peers)
    relay = NodeRelay(
        transport=transport,
        backhaul=RecordingBackhaul(),
        zone_id=1,
        phone_ping=phone_ping,
    )
    return relay, transport


def test_relay_demuxes_telemetry_to_the_service_never_the_pipeline():
    service, _, _ = make_service()
    _, transport = make_relay(["phoneA", "phoneB"], phone_ping=service)

    transport.deliver("phoneA", encode_pong(lat=3.0, lon=4.0, battery=55))

    assert transport.sent == []  # never relayed to phoneB
    (report,) = service.reports()
    assert (report["lat"], report["lon"], report["battery"]) == (3.0, 4.0, 55)


def test_relay_drops_telemetry_frames_even_without_a_service():
    _, transport = make_relay(["phoneA", "phoneB"])
    transport.deliver("phoneA", encode_pong(battery=10))
    assert transport.sent == []


def test_relay_still_relays_mesh_packets():
    service, _, _ = make_service()
    _, transport = make_relay(["phoneA", "phoneB"], phone_ping=service)
    transport.deliver("phoneA", build_packet(ttl=5, zone_id=1))
    assert [p for p, _ in transport.sent] == ["phoneB"]
    assert service.reports() == []


# -- heartbeat integration ----------------------------------------------------

def test_heartbeat_carries_phone_telemetry_reports():
    import time

    from node.monitoring.heartbeat_sender import HeartbeatSender
    from tests.test_heartbeat_sender import RecordingBackend

    service, _, _ = make_service()
    service.handle_frame("phoneA", encode_pong(lat=51.5, lon=-0.1, battery=84,
                                               charging=True))
    backend = RecordingBackend()
    try:
        sender = HeartbeatSender(
            node_id="test-node",
            zone_id=3,
            zone_name="Main Stage",
            base_url=backend.url,
            transport=FakeTransport(["phoneA"]),
            backhaul=RecordingBackhaul(),
            timeout_s=1.0,
            phone_ping=service,
        )
        sender._started_at = time.monotonic()
        sender.beat()

        deadline = time.time() + 2
        while not backend.received and time.time() < deadline:
            time.sleep(0.02)
        (beat,) = backend.received
        assert beat["phone_telemetry"] == {"reports": [{
            "peer_id": "phoneA",
            "lat": 51.5,
            "lon": -0.1,
            "battery": 84,
            "charging": True,
            "age_s": 0,
        }]}
    finally:
        backend.close()
