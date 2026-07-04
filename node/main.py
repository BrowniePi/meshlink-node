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
    from node.backhaul.batman_backhaul import BatmanBackhaul
    from node.backhaul.radio_config import check_backhaul_radio
    from node.ble import create_gatt_server
    from node.relay import NodeRelay
    from node.transport.ble_transport import BleTransport

    if config.BACKHAUL_ZONE_TABLE is None:
        check_backhaul_radio()  # diagnosis only — BLE service runs either way
    else:
        # Dev override (e.g. a Mac node on a plain LAN/loopback): no batman-adv
        # radio to check, the zone table is explicit rather than 10.77.0.x.
        log.info("backhaul using MESHLINK_ZONE_TABLE override — skipping "
                 "batman-adv radio check")

    server = create_gatt_server()
    transport = BleTransport(server)
    backhaul = BatmanBackhaul(
        zone_id=config.NODE_ZONE_ID,
        zone_table=config.BACKHAUL_ZONE_TABLE,  # None → the static 10.77.0.x table
        broadcast_addr=config.BACKHAUL_BROADCAST_ADDR,
        bind=("0.0.0.0", config.BACKHAUL_UDP_PORT),
    )
    relay = NodeRelay(
        transport=transport,
        backhaul=backhaul,
        zone_id=config.NODE_ZONE_ID,
    )

    backhaul.start()
    relay.start()
    log.info("node up — zone_id=%d, advertising MeshLink service", config.NODE_ZONE_ID)
    try:
        server.run_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        relay.stop()
        backhaul.stop()


if __name__ == "__main__":
    main()
