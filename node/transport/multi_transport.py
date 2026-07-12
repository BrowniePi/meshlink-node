"""Fan-out over several phone-facing transports (Phase 6: BLE + WiFi).

The relay pipeline speaks to exactly one Transport; this composite presents
BLE and WiFi as that one. Receives from every child are funneled into the
single callback, list_peers is the union, and send() routes by peer-id
prefix ("wifi:…" → the WiFi child, anything else → BLE) so a message that
arrived over one radio is relayed out over both.
"""
from typing import Callable

from node.core import Transport
from node.transport.wifi_transport import WIFI_PEER_PREFIX, WifiTransport


class MultiTransport(Transport):
    def __init__(self, ble: Transport, wifi: WifiTransport) -> None:
        self._ble = ble
        self._wifi = wifi

    def start(self) -> None:
        self._ble.start()
        self._wifi.start()

    def stop(self) -> None:
        self._wifi.stop()
        self._ble.stop()

    def send(self, peer_id: str, data: bytes) -> None:
        if peer_id.startswith(WIFI_PEER_PREFIX):
            self._wifi.send(peer_id, data)
        else:
            self._ble.send(peer_id, data)

    def on_receive(self, callback: Callable[[str, bytes], None]) -> None:
        self._ble.on_receive(callback)
        self._wifi.on_receive(callback)

    def on_connect(self, callback: Callable[[str], None]) -> None:
        """See BleTransport.on_connect — forwarded to both children."""
        self._ble.on_connect(callback)
        self._wifi.on_connect(callback)

    def list_peers(self) -> list[str]:
        return self._ble.list_peers() + self._wifi.list_peers()
