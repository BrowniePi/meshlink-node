"""MeshLink node entrypoint.

Phase 3: BLE GATT peripheral relaying messages between connected phones
via the shared meshlink-core pipeline, plus the batman-adv backhaul
relaying cross-zone traffic between nodes.
"""
import logging


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("meshlink.node")
    log.info("meshlink-node starting")

    # Heavy imports happen here so `python3 -m node.main --help`-style tooling
    # and unit tests never need BlueZ/D-Bus present.
    from node import config
    from node.backhaul.batman_backhaul import BatmanBackhaul
    from node.backhaul.radio_config import check_backhaul_radio
    from node.ble.gatt_server import GattServer
    from node.relay import NodeRelay
    from node.transport.ble_transport import BleTransport

    check_backhaul_radio()  # diagnosis only — BLE service runs either way

    server = GattServer()
    transport = BleTransport(server)
    backhaul = BatmanBackhaul(zone_id=config.NODE_ZONE_ID)
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
