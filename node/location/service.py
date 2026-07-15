"""Node-terminated location message handling, hooked into NodeRelay.

Runs strictly AFTER the full 8-step pipeline accepts a packet — a location
query is still a signed, attested, rate-limited message; nothing here
bypasses steps 1–7. This service only takes over step 8 routing for the
message types the node itself terminates:

  LOCATION (0x02)    — the 120 s beacon. Teed into the latest-coordinate-only
                       store and TERMINATED: a raw coordinate must never be
                       fanned out to every phone in the zone.
  LOCATION_QUERY     — capability check (location/authz.py); on success the
                       node signs a LOCATION_RESPONSE with its own identity
                       and sends it straight back over the requester's
                       session. Either way the query reports "not handled"
                       so the relay still sprays it toward the target phone,
                       which answers with a live fix — the node's cached
                       answer is the fallback for a target that is asleep or
                       out of reach; the requester keeps the freshest.
  LOCATION_REVOKE    — feeds the revocation set, then reports "not handled"
                       so the relay still fans it out (target → node AND
                       target → friend).
"""
import logging
import time
from typing import Callable

from nacl.utils import random as nacl_random

from node.core import (
    Message,
    MessageType,
    Transport,
    build_signed_packet,
    decode_location_beacon,
)
from node.location.authz import LocationAuthz
from node.location.store import LocationStore

log = logging.getLogger("meshlink.location")

RESPONSE_TTL = 3

# The requester may be phone-hops away now that queries spray beyond the
# node's own cell — give the response a real Spray-and-Wait copy budget
# (routing Case 2) instead of the old single direct-session copy.
RESPONSE_SPRAY_L = 8


class LocationService:
    def __init__(
        self,
        store: LocationStore,
        authz: LocationAuthz,
        transport: Transport,
        node_identity,           # core identity.DeviceIdentity
        zone_id: int,
        clock: Callable[[], float] = time.time,
    ):
        self._store = store
        self._authz = authz
        self._transport = transport
        self._identity = node_identity
        self._zone_id = zone_id
        self._clock = clock
        # The node is infrastructure, not a phone: a per-boot random ephem_id
        # satisfies the envelope format without pretending to be a rotating
        # phone identifier. It appears only inside packets on established
        # sessions, never in BLE advertising (invariant 2).
        self._ephem_id = nacl_random(16)

    def handle_node_terminated(self, peer_id: str, msg: Message) -> bool:
        """Returns True when the message is consumed here (relay must NOT fan
        it out); False when normal step 8 relaying should continue."""
        if msg.msg_type == MessageType.LOCATION:
            self._ingest_beacon(msg)
            return True

        if msg.msg_type == MessageType.LOCATION_QUERY:
            response_payload = self._authz.handle_query(msg)
            if response_payload is not None:
                packet = build_signed_packet(
                    self._identity,
                    ephem_id=self._ephem_id,
                    ttl=RESPONSE_TTL,
                    spray_l=RESPONSE_SPRAY_L,
                    zone_id=self._zone_id,
                    msg_type=MessageType.LOCATION_RESPONSE,
                    payload=response_payload,
                )
                self._transport.send(peer_id, packet)
            # Refusal is silent and uniform — nothing is sent either way
            # beyond the success path (§8.3). Answered or not, the query
            # keeps spraying toward the target phone (hybrid: the phone's
            # live fix outranks this cached one; the token is only usable
            # by its grantee, so onward relay leaks metadata, not location).
            return False

        if msg.msg_type == MessageType.LOCATION_REVOKE:
            self._authz.handle_revoke(msg)
            return False  # also relay onward so the friend's phone learns

        return False

    def _ingest_beacon(self, msg: Message) -> None:
        try:
            lat, lon, accuracy_m = decode_location_beacon(msg.payload)
        except ValueError:
            log.info("malformed LOCATION beacon from %s dropped",
                     msg.sender_key.hex()[:16])
            return
        # Overwrite, never append (invariant 3). Keyed by the Ed25519-verified
        # envelope sender_key — the stable identity ↔ location mapping exists
        # only here, node-internal (invariant 2).
        self._store.update(
            msg.sender_key,
            lat_microdeg=lat,
            lon_microdeg=lon,
            accuracy_m=accuracy_m,
            zone_id=msg.zone_id,
        )
