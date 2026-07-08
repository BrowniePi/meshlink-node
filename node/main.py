"""MeshLink node entrypoint.

Phase 3: BLE GATT peripheral relaying messages between connected phones
via the shared meshlink-core pipeline, plus the batman-adv backhaul
relaying cross-zone traffic between nodes.
"""
import logging
import sys
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "node.log"


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # A dropped or scrolled-past terminal line is the single biggest source
    # of "did it even receive that" confusion during manual phone testing —
    # everything also lands in a file so `tail -f node.log` is always
    # available as ground truth regardless of what the terminal shows.
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])


def main() -> None:
    _configure_logging()
    log = logging.getLogger("meshlink.node")
    log.info("meshlink-node starting — logging to %s", LOG_FILE)

    # Heavy imports happen here so `python3 -m node.main --help`-style tooling
    # and unit tests never need the platform Bluetooth stack present.
    from node import config
    from node.attestation.organiser_key import load_organiser_pubkey
    from node.backhaul.batman_backhaul import BatmanBackhaul
    from node.backhaul.dynamic_zone_table import DynamicZoneTable
    from node.backhaul.radio_config import check_backhaul_radio
    from node.backhaul.base import LoggingStubBackhaul
    from node.backhaul.zone_sync import ZoneSync
    from node.ble import create_gatt_server
    from node.core import AttestationCache
    from node.monitoring.heartbeat_sender import HeartbeatSender
    from node.monitoring.phone_ping import PhonePingService
    from node.relay import NodeRelay
    from node.transport.ble_transport import BleTransport
    from node.transport.multi_transport import MultiTransport
    from node.transport.wifi_transport import WifiTransport

    # Phase 5: attestation enforcement. One backend call per boot (or none,
    # with the env override); every token verification afterwards is offline.
    if config.ORGANISER_PUBKEY:
        organiser_pubkey = config.ORGANISER_PUBKEY
        log.info("organiser public key from MESHLINK_ORGANISER_PUBKEY: %s",
                 organiser_pubkey)
    else:
        organiser_pubkey = load_organiser_pubkey(
            config.BACKEND_BASE_URL, config.ORGANISER_KEY_CACHE
        )
    attestation = AttestationCache(bytes.fromhex(organiser_pubkey), config.EVENT_ID)
    log.info("attestation enforcement on — event_id=%s", config.EVENT_ID)

    if config.BACKHAUL_ZONE_TABLE is None:
        check_backhaul_radio()  # diagnosis only — BLE service runs either way
    else:
        # Dev override (e.g. a Mac node on a plain LAN/loopback): no batman-adv
        # radio to check, the zone table is explicit rather than 10.77.0.x.
        log.info("backhaul using MESHLINK_ZONE_TABLE override — skipping "
                 "batman-adv radio check")

    server = create_gatt_server()
    transport = BleTransport(server)
    ap_provisioner = None
    if config.WIFI_LISTEN.lower() != "off":
        # Phase 6: serve phones over the hostapd AP as well. Binding fails
        # harmlessly on machines without the AP interface (BLE-only, exactly
        # Phase 5 behavior).
        wifi_host, wifi_port = config.parse_addr(config.WIFI_LISTEN)
        transport = MultiTransport(transport, WifiTransport(wifi_host, wifi_port))
        ap_provisioner = _provision_ap(log)
    # Phase 7: the zone→node table is learned at runtime over the backhaul
    # (zone-sync gossip), not hand-wired. MESHLINK_ZONE_TABLE, if set, seeds it
    # with never-expiring operator-pinned entries for dev nodes that have no
    # mesh to learn from; a fresh learned entry always wins over the seed.
    zone_table = DynamicZoneTable(
        own_zone_id=config.NODE_ZONE_ID,
        seed=config.BACKHAUL_ZONE_TABLE,
        entry_ttl_s=config.ZONE_ENTRY_TTL_S,
    )
    backhaul = BatmanBackhaul(
        zone_id=config.NODE_ZONE_ID,
        table=zone_table,
        own_addr=config.BACKHAUL_ADVERTISE_ADDR,
        broadcast_addr=config.BACKHAUL_BROADCAST_ADDR,
        bind=("0.0.0.0", config.BACKHAUL_UDP_PORT),
    )
    # Gossip our zone assignment to peers and fold in theirs, re-syncing each
    # heartbeat interval (docs/phase7-node-decisions.md).
    zone_sync = ZoneSync(
        backhaul=backhaul,
        table=zone_table,
        node_id=config.NODE_ID,
        zone_id=config.NODE_ZONE_ID,
        own_addr=config.BACKHAUL_ADVERTISE_ADDR,
        interval_s=config.HEARTBEAT_INTERVAL_S,
    )
    # Phase 7: ask each connected phone for location + battery every 2 min;
    # the latest answers ride the heartbeat as phone_telemetry.
    phone_ping = PhonePingService(
        transport=transport,
        interval_s=config.PHONE_PING_INTERVAL_S,
    )
    relay = NodeRelay(
        transport=transport,
        backhaul=backhaul,
        zone_id=config.NODE_ZONE_ID,
        attestation=attestation,
        phone_ping=phone_ping,
    )

    heartbeat = HeartbeatSender(
        node_id=config.NODE_ID,
        zone_id=config.NODE_ZONE_ID,
        zone_name=config.NODE_ZONE_NAME,
        base_url=config.BACKEND_BASE_URL,
        transport=transport,
        backhaul=backhaul,
        relay=relay,
        phone_ping=phone_ping,
        interval_s=config.HEARTBEAT_INTERVAL_S,
    )

    backhaul.start()
    zone_sync.start()
    relay.start()
    phone_ping.start()
    heartbeat.start()
    log.info("node up — zone_id=%d, advertising MeshLink service", config.NODE_ZONE_ID)
    try:
        server.run_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        heartbeat.stop()
        phone_ping.stop()
        relay.stop()
        zone_sync.stop()
        backhaul.stop()
        if ap_provisioner is not None:
            ap_provisioner.stop()


def _provision_ap(log):
    """Bring up the phone-facing AP if this platform/config wants us to.

    Best-effort: any failure downgrades to BLE + WiFi-listener (Phase 5
    behavior), never aborts the node. Returns the provisioner (so its stop()
    can tear the AP down on shutdown) or None.
    """
    from node import config
    mode = config.WIFI_AP_PROVISION
    if mode == "off":
        return None
    if mode == "auto" and sys.platform != "darwin":
        # Pi default: scripts/setup_hostapd.sh + systemd own the AP out of
        # band. MESHLINK_AP_PROVISION=on forces the node to drive it instead.
        return None

    from node.wifi_ap import create_ap_provisioner
    from node.wifi_ap.deployment_config import load_deployment_config
    try:
        deployment = load_deployment_config()
    except ValueError as exc:
        log.warning("phone-facing AP not provisioned: %s", exc)
        return None

    provisioner = create_ap_provisioner(deployment)
    if provisioner.start():
        log.info("phone-facing AP up — SSID %r", deployment.ssid)
    else:
        log.info("phone-facing AP not brought up — serving WiFi listener + "
                 "BLE only")
    return provisioner


if __name__ == "__main__":
    main()
