"""Node relay: transport → meshlink-core pipeline → connected peers.

The node is never a message destination — anything the pipeline accepts is
relayed to the node's other connected phones, and cross-node cases are handed
to the NodeBackhaul interface (a logging stub until Phase 3).
"""
import logging
import threading
import time

from node.attestation import MSG_TYPE_ATTESTATION_PRESENT
from node.backhaul.base import NodeBackhaul
from node.core import AttestationCache, Outcome, RelayPipeline, Transport, parse_packet
from node.location.service import LocationService
from node.monitoring.phone_ping import PhonePingService, is_telemetry_frame

log = logging.getLogger("meshlink.relay")

BROADCAST_ZONE = 0xFFFF

# ttl byte offset in the fixed header (docs/message-format.md §2).
_TTL_OFFSET = 68


def _describe_payload(payload: bytes) -> str:
    """Best-effort human-readable payload for logging.

    Payloads are plaintext until Phase 4 adds encryption, so UTF-8 text is
    the common case; anything else falls back to a hex preview.
    """
    try:
        return repr(payload.decode("utf-8"))
    except UnicodeDecodeError:
        return f"<{len(payload)} bytes: {payload[:32].hex()}{'…' if len(payload) > 32 else ''}>"


def _summarize_packet(raw: bytes) -> str:
    """Best-effort structural summary of a packet, independent of the
    pipeline's own parse.

    A packet can fail any check from step 2 onward (ttl/timestamp/dedup/
    rate-limit/signature/attestation) after having parsed just fine — but
    meshlink-core's PipelineResult only carries the parsed Message on
    Outcome.DELIVER, never on Outcome.DROP. Re-parsing here independently is
    the only way to see *which* sender/message a given drop was about, which
    is what actually matters when debugging "why does this phone keep
    getting dropped."
    """
    try:
        msg = parse_packet(raw)
    except ValueError as exc:
        return f"<unparseable, {len(raw)} bytes: {exc}>"
    age_s = int(time.time()) - msg.timestamp
    return (
        f"msg_id={msg.msg_id.hex()[:8]} sender={msg.sender_key.hex()[:16]} "
        f"ephem={msg.ephem_id.hex()[:8]} ttl={msg.ttl} zone={msg.zone_id} "
        f"msg_type={msg.msg_type} age={age_s}s payload={_describe_payload(msg.payload)}"
    )


def decrement_ttl(raw: bytes) -> bytes:
    """Return the packet with ttl reduced by one hop.

    Safe against the Phase 4 Ed25519 check: the signed region deliberately
    excludes the hop-mutable ttl and spray_L bytes (offsets 68-69, see
    meshlink-core pipeline/message.py:signed_region and DECISIONS.md), so a
    relay decrementing ttl here does not invalidate the sender's signature.
    """
    ttl = raw[_TTL_OFFSET]
    return raw[:_TTL_OFFSET] + bytes([ttl - 1]) + raw[_TTL_OFFSET + 1:]


class NodeRelay:
    def __init__(
        self,
        transport: Transport,
        backhaul: NodeBackhaul,
        zone_id: int,
        attestation: AttestationCache | None = None,
        phone_ping: PhonePingService | None = None,
        location: LocationService | None = None,
    ):
        self._transport = transport
        self._backhaul = backhaul
        self._zone_id = zone_id
        self._attestation = attestation
        self._phone_ping = phone_ping
        self._location = location
        self._pipeline = RelayPipeline(attestation=attestation)
        # Traffic counters since boot, reported in heartbeats. Guarded by a
        # lock because BLE and backhaul receives arrive on different threads.
        self._stats_lock = threading.Lock()
        self._stats = {
            "received": 0,             # packets handed to the relay
            "accepted": 0,             # passed the pipeline
            "dropped": 0,              # rejected by the pipeline
            "relayed_to_phones": 0,    # accepted msgs fanned out locally
            "forwarded_cross_zone": 0, # sent to one other zone via backhaul
            "broadcast_to_nodes": 0,   # flooded to every node via backhaul
            "attestations_cached": 0,  # token presentations accepted
            "location_terminated": 0,  # beacons/queries consumed at this node
        }
        # Presentation packets validate structure/replay/signature like any
        # other packet, but must never be gated on attestation themselves —
        # a sender presenting its first token isn't attested yet.
        self._presentation_pipeline = RelayPipeline()
        transport.on_receive(self._handle_packet)
        # Packets arriving from other nodes run the same pipeline as BLE
        # traffic (dedup is what stops cross-node loops). No-op on the stub.
        backhaul.on_receive(self._handle_packet)

    def _count(self, key: str, n: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] += n

    def stats(self) -> dict:
        """Snapshot of the since-boot traffic counters (for heartbeats)."""
        with self._stats_lock:
            return dict(self._stats)

    def start(self) -> None:
        self._transport.start()

    def stop(self) -> None:
        self._transport.stop()

    def _handle_packet(self, peer_id: str, raw: bytes) -> None:
        # Phase 7 telemetry demux: MLPP1 control frames (phone-ping pongs)
        # are not signed mesh packets and must never enter the pipeline, be
        # relayed to other phones, or count as relay traffic — a phone's
        # location/battery leaves this node only as aggregated heartbeat
        # telemetry.
        if is_telemetry_frame(raw):
            if self._phone_ping is not None:
                self._phone_ping.handle_frame(peer_id, raw)
            return

        log.info(
            "received %d-byte packet from %s: %s",
            len(raw), peer_id, _summarize_packet(raw),
        )
        self._count("received")

        if self._attestation is not None and _is_attestation_presentation(raw):
            self._handle_presentation(peer_id, raw)
            return

        result = self._pipeline.process(raw)
        if result.outcome == Outcome.DROP:
            log.info("drop from %s: %s", peer_id, result.drop_reason)
            self._count("dropped")
            return

        msg = result.message
        self._count("accepted")
        log.info(
            "accepted msg %s from %s: %s",
            msg.msg_id.hex()[:8], peer_id, _describe_payload(msg.payload),
        )

        # Node-terminated location traffic (Phase 5 friendship extension).
        # Runs after the full pipeline accepted the packet — the capability
        # check is an additional step after step 8 routing, never a bypass of
        # steps 1-7. Consumed messages (beacons, queries) are NOT fanned out:
        # a raw coordinate or a location query must never reach other phones.
        if self._location is not None and self._location.handle_node_terminated(
            peer_id, msg
        ):
            self._count("location_terminated")
            return

        # Cross-node cases go through the backhaul interface. With one node
        # it's a logging stub — but routing already speaks in these terms so
        # Phase 3 only has to supply a real implementation.
        if msg.zone_id == BROADCAST_ZONE:
            self._backhaul.broadcast_to_all_nodes(raw)
            self._count("broadcast_to_nodes")
        elif msg.zone_id != self._zone_id:
            self._backhaul.forward_to_zone(msg.zone_id, raw)
            self._count("forwarded_cross_zone")

        # Phase 2: single node, single zone — every accepted message is also
        # relayed to the local BLE cell regardless of its zone_id.
        relayed = decrement_ttl(raw)
        for peer in self._transport.list_peers():
            if peer == peer_id:
                continue
            self._transport.send(peer, relayed)
        self._count("relayed_to_phones")
        log.info(
            "relayed msg %s from %s (zone %d, ttl %d→%d)",
            msg.msg_id.hex()[:8], peer_id, msg.zone_id, msg.ttl, msg.ttl - 1,
        )

    def _handle_presentation(self, peer_id: str, raw: bytes) -> None:
        """Validate a token presentation and cache its sender — never
        delivered to phones (a JWT blob means nothing at the app layer), but
        spread over the backhaul so other nodes learn the sender too."""
        result = self._presentation_pipeline.process(raw)
        if result.outcome == Outcome.DROP:
            log.info("presentation drop from %s: %s", peer_id, result.drop_reason)
            self._count("dropped")
            return

        msg = result.message
        try:
            sender_key = self._attestation.add_token(msg.payload.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            log.info("attestation presentation from %s rejected: %s", peer_id, exc)
            self._count("dropped")
            return
        self._count("attestations_cached")

        log.info(
            "attestation presentation from %s accepted — sender %s cached, "
            "not delivered to phones",
            peer_id, sender_key.hex()[:16],
        )
        if msg.zone_id == BROADCAST_ZONE:
            self._backhaul.broadcast_to_all_nodes(raw)
        elif msg.zone_id != self._zone_id:
            self._backhaul.forward_to_zone(msg.zone_id, raw)


def _is_attestation_presentation(raw: bytes) -> bool:
    try:
        return parse_packet(raw).msg_type == MSG_TYPE_ATTESTATION_PRESENT
    except ValueError:
        return False
