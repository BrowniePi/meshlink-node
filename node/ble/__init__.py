"""BLE peripheral backends.

Backend imports are lazy so that importing node.ble never pulls in
platform-specific bindings (dbus/PyGObject on Linux, PyObjC on macOS).
"""
import os
import sys


def create_gatt_server():
    """Instantiate the GATT server backend for this platform.

    Override with MESHLINK_BLE_BACKEND=bluez|corebluetooth if needed.
    """
    backend = os.environ.get("MESHLINK_BLE_BACKEND")
    if not backend:
        backend = "corebluetooth" if sys.platform == "darwin" else "bluez"
    if backend == "corebluetooth":
        from node.ble.corebluetooth import CoreBluetoothGattServer
        return CoreBluetoothGattServer()
    if backend == "bluez":
        from node.ble.bluez import BluezGattServer
        return BluezGattServer()
    raise ValueError(f"unknown MESHLINK_BLE_BACKEND {backend!r}")
