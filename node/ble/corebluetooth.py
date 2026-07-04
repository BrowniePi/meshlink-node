"""CoreBluetooth GATT server backend — macOS (development machines).

Same peripheral role as the BlueZ backend: advertise the MeshLink service,
accept phone writes on RX, push notifications on TX. All shared behaviour
lives in node/ble/base.py; this module is only the CBPeripheralManager
plumbing, driven through PyObjC.

macOS platform notes (differences from BlueZ that matter):
- The first run triggers the system Bluetooth permission prompt for whatever
  app hosts the Python process (Terminal, iTerm, VS Code). Nothing works
  until it is granted (System Settings > Privacy & Security > Bluetooth).
- CoreBluetooth's peripheral API has no connect/disconnect events; a
  central subscribing to / unsubscribing from the TX characteristic is the
  closest proxy, so peer tracking keys off TX subscription. The phone app
  subscribes immediately after connecting, so in practice this matches.
- peer_id is the CBCentral identifier UUID (macOS hides raw MAC addresses).
- updateValue can return False when the notification queue is full; chunks
  are buffered in order and flushed on IsReadyToUpdateSubscribers.
"""
import logging
from collections import deque

import objc
from CoreBluetooth import (
    CBAdvertisementDataLocalNameKey,
    CBAdvertisementDataServiceUUIDsKey,
    CBATTErrorSuccess,
    CBAttributePermissionsReadable,
    CBAttributePermissionsWriteable,
    CBCharacteristicPropertyNotify,
    CBCharacteristicPropertyWrite,
    CBCharacteristicPropertyWriteWithoutResponse,
    CBManagerStatePoweredOn,
    CBMutableCharacteristic,
    CBMutableService,
    CBPeripheralManager,
    CBUUID,
)
from CoreFoundation import CFRunLoopGetCurrent, CFRunLoopRun, CFRunLoopStop
from Foundation import NSObject

from node import config
from node.ble.base import GattServerBase

log = logging.getLogger("meshlink.ble")


class _PeripheralDelegate(NSObject):
    """Objective-C delegate that forwards CBPeripheralManager callbacks."""

    def initWithServer_(self, server):
        self = objc.super(_PeripheralDelegate, self).init()
        if self is None:
            return None
        self._server = server
        return self

    def peripheralManagerDidUpdateState_(self, manager):
        self._server._state_changed()

    def peripheralManager_didAddService_error_(self, manager, service, error):
        self._server._service_added(error)

    def peripheralManagerDidStartAdvertising_error_(self, manager, error):
        if error is not None:
            log.error("advertising failed: %s", error)
        else:
            log.info("advertising MeshLink service %s", config.MESH_SERVICE_UUID)

    def peripheralManager_didReceiveWriteRequests_(self, manager, requests):
        self._server._write_requests(requests)

    def peripheralManager_central_didSubscribeToCharacteristic_(
        self, manager, central, characteristic
    ):
        self._server._peer_connected(str(central.identifier().UUIDString()))

    def peripheralManager_central_didUnsubscribeFromCharacteristic_(
        self, manager, central, characteristic
    ):
        self._server._peer_disconnected(str(central.identifier().UUIDString()))

    def peripheralManagerIsReadyToUpdateSubscribers_(self, manager):
        self._server._flush_pending()


class CoreBluetoothGattServer(GattServerBase):
    """CoreBluetooth backend; owns the CBPeripheralManager plumbing."""

    def __init__(self) -> None:
        super().__init__()
        self._delegate = _PeripheralDelegate.alloc().initWithServer_(self)
        self._manager = None
        self._tx_char = None
        self._rx_uuid = CBUUID.UUIDWithString_(config.RX_CHAR_UUID)
        self._runloop = None
        self._pending: deque[bytes] = deque()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        # Creating the manager triggers the macOS Bluetooth permission prompt
        # on first run; service registration continues in _state_changed once
        # the adapter reports powered-on. queue=None delivers callbacks on the
        # main dispatch queue, which run_forever's CFRunLoop drains — so
        # run_forever must be called on the main thread.
        self._manager = CBPeripheralManager.alloc().initWithDelegate_queue_(
            self._delegate, None
        )

    def run_forever(self) -> None:
        self._runloop = CFRunLoopGetCurrent()
        CFRunLoopRun()

    def stop(self) -> None:
        if self._manager is not None and self._manager.isAdvertising():
            self._manager.stopAdvertising()
        if self._runloop is not None:
            CFRunLoopStop(self._runloop)

    # -- CBPeripheralManager events -------------------------------------------

    def _state_changed(self) -> None:
        state = self._manager.state()
        if state != CBManagerStatePoweredOn:
            log.warning(
                "CoreBluetooth state %d — waiting for powered-on "
                "(is Bluetooth on and permission granted?)", state
            )
            return
        self._register_service()

    def _register_service(self) -> None:
        rx = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            self._rx_uuid,
            CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse,
            None,
            CBAttributePermissionsWriteable,
        )
        tx = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(config.TX_CHAR_UUID),
            CBCharacteristicPropertyNotify,
            None,
            CBAttributePermissionsReadable,
        )
        service = CBMutableService.alloc().initWithType_primary_(
            CBUUID.UUIDWithString_(config.MESH_SERVICE_UUID), True
        )
        service.setCharacteristics_([rx, tx])
        self._tx_char = tx
        self._manager.addService_(service)

    def _service_added(self, error) -> None:
        if error is not None:
            log.error("addService failed: %s", error)
            self.stop()
            return
        log.info("GATT service registered")
        self._manager.startAdvertising_({
            CBAdvertisementDataServiceUUIDsKey: [
                CBUUID.UUIDWithString_(config.MESH_SERVICE_UUID)
            ],
            CBAdvertisementDataLocalNameKey: config.BLE_LOCAL_NAME,
        })

    def _write_requests(self, requests) -> None:
        for request in requests:
            if not request.characteristic().UUID().isEqual_(self._rx_uuid):
                continue
            value = request.value()
            if value is None:
                continue
            peer_id = str(request.central().identifier().UUIDString())
            self._handle_chunk(peer_id, bytes(value))
        # CoreBluetooth expects exactly one response per delivery batch.
        self._manager.respondToRequest_withResult_(requests[0], CBATTErrorSuccess)

    # -- outbound ------------------------------------------------------------

    def _notify_chunk(self, data: bytes) -> None:
        if self._tx_char is None:
            return
        if self._pending:
            # Preserve chunk order behind the existing backlog.
            self._pending.append(data)
            return
        if not self._manager.updateValue_forCharacteristic_onSubscribedCentrals_(
            data, self._tx_char, None
        ):
            self._pending.append(data)

    def _flush_pending(self) -> None:
        while self._pending:
            sent = self._manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                self._pending[0], self._tx_char, None
            )
            if not sent:
                return
            self._pending.popleft()
