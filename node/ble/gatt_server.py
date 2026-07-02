"""BlueZ D-Bus GATT server — the node acting as a BLE peripheral.

Advertises the MeshLink service UUID as a full, standard advertisement that
both iOS and Android can discover (this is the node side of the architecture
decision that phones always connect outbound to a node). Phones write framed
packets to the RX characteristic; the node pushes framed packets to phones
via notifications on the TX characteristic.

Requires BlueZ (tested against 5.66, Raspberry Pi OS Bookworm default) with
dbus-python + PyGObject. See node/ble/README.md for D-Bus names and paths.
"""
import logging
from typing import Callable, Optional

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from node import config
from node.ble.framing import FrameAssembler, chunk, frame

log = logging.getLogger("meshlink.ble")

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_AD_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_AD_IFACE = "org.bluez.LEAdvertisement1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

APP_PATH = "/com/meshlink/node"


class Advertisement(dbus.service.Object):
    """LE advertisement carrying the full MeshLink service UUID."""

    PATH = APP_PATH + "/advertisement0"

    def __init__(self, bus: dbus.SystemBus) -> None:
        super().__init__(bus, self.PATH)

    def properties(self) -> dict:
        return {
            "Type": "peripheral",
            "ServiceUUIDs": dbus.Array([config.MESH_SERVICE_UUID], signature="s"),
            "LocalName": dbus.String(config.BLE_LOCAL_NAME),
            "Discoverable": dbus.Boolean(True),
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_AD_IFACE:
            raise dbus.exceptions.DBusException("org.bluez.Error.InvalidArguments")
        return self.properties()

    @dbus.service.method(LE_AD_IFACE)
    def Release(self):
        log.info("advertisement released by BlueZ")


class Characteristic(dbus.service.Object):
    """Minimal GattCharacteristic1 base."""

    def __init__(self, bus, path, uuid, flags, service_path):
        self.path = path
        self.uuid = uuid
        self.flags = flags
        self.service_path = service_path
        super().__init__(bus, path)

    def properties(self) -> dict:
        return {
            GATT_CHRC_IFACE: {
                "Service": dbus.ObjectPath(self.service_path),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise dbus.exceptions.DBusException("org.bluez.Error.InvalidArguments")
        return self.properties()[GATT_CHRC_IFACE]


class RxCharacteristic(Characteristic):
    """Phones (centrals) write framed packets here."""

    def __init__(self, bus, service_path, on_chunk: Callable[[str, bytes], None]):
        super().__init__(
            bus,
            service_path + "/char_rx",
            config.RX_CHAR_UUID,
            ["write", "write-without-response"],
            service_path,
        )
        self._on_chunk = on_chunk

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        device = str(options.get("device", "unknown"))
        self._on_chunk(device, bytes(bytearray(value)))


class TxCharacteristic(Characteristic):
    """Node notifies framed outbound packets here.

    BlueZ delivers a Value PropertiesChanged notification to every subscribed
    central — per-peer unicast is not possible with a shared GATT
    characteristic, so outbound packets fan out to all connected phones and
    the phone-side pipeline's msg_id dedup discards copies not meant for it.
    Acceptable at Phase 2 scale (a handful of phones on one node).
    """

    def __init__(self, bus, service_path):
        super().__init__(
            bus,
            service_path + "/char_tx",
            config.TX_CHAR_UUID,
            ["notify"],
            service_path,
        )
        self.notifying = False

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        self.notifying = True
        log.info("a central subscribed to TX notifications")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        self.notifying = False
        log.info("a central unsubscribed from TX notifications")

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def notify_chunk(self, data: bytes) -> None:
        if not self.notifying:
            return
        self.PropertiesChanged(
            GATT_CHRC_IFACE,
            {"Value": dbus.Array(data, signature="y")},
            [],
        )


class MeshService(dbus.service.Object):
    """GattService1 exposing the MeshLink RX/TX characteristics."""

    PATH = APP_PATH + "/service0"

    def __init__(self, bus, on_chunk):
        super().__init__(bus, self.PATH)
        self.rx = RxCharacteristic(bus, self.PATH, on_chunk)
        self.tx = TxCharacteristic(bus, self.PATH)

    def properties(self) -> dict:
        return {
            GATT_SERVICE_IFACE: {
                "UUID": config.MESH_SERVICE_UUID,
                "Primary": dbus.Boolean(True),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("org.bluez.Error.InvalidArguments")
        return self.properties()[GATT_SERVICE_IFACE]


class Application(dbus.service.Object):
    """ObjectManager root BlueZ walks to discover the GATT tree."""

    def __init__(self, bus, service: MeshService):
        self.service = service
        super().__init__(bus, APP_PATH)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {
            self.service.PATH: self.service.properties(),
            self.service.rx.path: self.service.rx.properties(),
            self.service.tx.path: self.service.tx.properties(),
        }


class GattServer:
    """Facade the transport layer uses; owns the D-Bus plumbing.

    Callbacks:
      on_packet(peer_id, packet)   — complete reassembled inbound packet
      on_disconnect(peer_id)       — a tracked central disconnected
    """

    def __init__(self) -> None:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._loop = GLib.MainLoop()
        self._service = MeshService(self._bus, self._handle_chunk)
        self._app = Application(self._bus, self._service)
        self._advertisement = Advertisement(self._bus)
        self._assemblers: dict[str, FrameAssembler] = {}
        self._connected: set[str] = set()
        self.on_packet: Optional[Callable[[str, bytes], None]] = None
        self.on_disconnect: Optional[Callable[[str], None]] = None

        self._bus.add_signal_receiver(
            self._device_properties_changed,
            dbus_interface=DBUS_PROP_IFACE,
            signal_name="PropertiesChanged",
            arg0=DEVICE_IFACE,
            path_keyword="path",
        )

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        adapter_path = self._find_adapter()
        adapter_props = dbus.Interface(
            self._bus.get_object(BLUEZ, adapter_path), DBUS_PROP_IFACE
        )
        adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))

        gatt_mgr = dbus.Interface(
            self._bus.get_object(BLUEZ, adapter_path), GATT_MANAGER_IFACE
        )
        gatt_mgr.RegisterApplication(
            APP_PATH, {},
            reply_handler=lambda: log.info("GATT application registered"),
            error_handler=lambda e: self._fatal(f"RegisterApplication failed: {e}"),
        )

        ad_mgr = dbus.Interface(
            self._bus.get_object(BLUEZ, adapter_path), LE_AD_MANAGER_IFACE
        )
        ad_mgr.RegisterAdvertisement(
            Advertisement.PATH, {},
            reply_handler=lambda: log.info(
                "advertising MeshLink service %s", config.MESH_SERVICE_UUID
            ),
            error_handler=lambda e: self._fatal(f"RegisterAdvertisement failed: {e}"),
        )

    def run_forever(self) -> None:
        self._loop.run()

    def stop(self) -> None:
        self._loop.quit()

    def _fatal(self, message: str) -> None:
        log.error(message)
        self.stop()

    def _find_adapter(self) -> str:
        om = dbus.Interface(self._bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE)
        for path, ifaces in om.GetManagedObjects().items():
            if GATT_MANAGER_IFACE in ifaces and LE_AD_MANAGER_IFACE in ifaces:
                return path
        raise RuntimeError("no BLE adapter with GATT + advertising support found")

    # -- inbound -------------------------------------------------------------

    def _handle_chunk(self, peer_id: str, data: bytes) -> None:
        self._connected.add(peer_id)
        assembler = self._assemblers.setdefault(peer_id, FrameAssembler())
        try:
            packets = assembler.feed(data)
        except ValueError as exc:
            log.warning("dropping corrupt stream from %s: %s", peer_id, exc)
            return
        for packet in packets:
            if self.on_packet is not None:
                self.on_packet(peer_id, packet)

    def _device_properties_changed(self, interface, changed, invalidated, path=None):
        if "Connected" not in changed or path is None:
            return
        peer_id = str(path)
        if changed["Connected"]:
            self._connected.add(peer_id)
            log.info("central connected: %s", peer_id)
        else:
            self._connected.discard(peer_id)
            self._assemblers.pop(peer_id, None)
            if self.on_disconnect is not None:
                self.on_disconnect(peer_id)
            log.info("central disconnected: %s", peer_id)

    # -- outbound ------------------------------------------------------------

    def send_packet(self, peer_id: str, packet: bytes) -> None:
        """Notify a framed packet out the TX characteristic.

        peer_id is accepted for interface symmetry but the notification fans
        out to every subscribed central (see TxCharacteristic docstring).
        """
        for piece in chunk(frame(packet), config.BLE_NOTIFY_CHUNK):
            self._service.tx.notify_chunk(piece)

    def peers(self) -> list[str]:
        return sorted(self._connected)
