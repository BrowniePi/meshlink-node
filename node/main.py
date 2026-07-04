"""MeshLink node entrypoint.

Phase 2: single node acting as a BLE GATT peripheral, relaying messages
between connected phones via the shared meshlink-core pipeline.
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
