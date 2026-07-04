"""MeshLink node entrypoint.

Phase 2: single node acting as a BLE GATT peripheral, relaying messages
between connected phones via the shared meshlink-core pipeline.
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
    # and unit tests never need the platform Bluetooth stack present.
    from node import config
    from node.backhaul.base import LoggingStubBackhaul
    from node.ble import create_gatt_server
    from node.relay import NodeRelay
    from node.transport.ble_transport import BleTransport

    server = create_gatt_server()
    transport = BleTransport(server)
    relay = NodeRelay(
        transport=transport,
        backhaul=LoggingStubBackhaul(),
        zone_id=config.NODE_ZONE_ID,
    )

    relay.start()
    log.info("node up — zone_id=%d, advertising MeshLink service", config.NODE_ZONE_ID)
    try:
        server.run_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        relay.stop()


if __name__ == "__main__":
    main()
