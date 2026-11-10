"""Read the Helix editor interface using the libusb shipped with HX Edit."""

import usb.backend.libusb1
import usb.core
import usb.util

from nam_inventory import PRODUCT_IDS, scan_link


class UsbLink:
    def __init__(self, device):
        self.device = device
        interface = device.get_active_configuration()[(0, 0)]
        endpoints = {usb.util.endpoint_direction(ep.bEndpointAddress): ep for ep in interface
                     if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK}
        if interface.bInterfaceClass != 0xFF or interface.bNumEndpoints != 2 or len(endpoints) != 2:
            raise OSError('The Helix editor interface has unexpected USB endpoints.')
        self.input = endpoints[usb.util.ENDPOINT_IN]
        self.output = endpoints[usb.util.ENDPOINT_OUT]
        self.mtu = self.input.wMaxPacketSize

    def write(self, data, _description):
        if self.device.write(self.output, data, timeout=2000) != len(data):
            raise OSError('Short Helix USB write.')

    def read(self, timeout_ms=100):
        try:
            return bytes(self.device.read(self.input, max(self.mtu, 512), timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return b''


def scan_profiles(firmware_tools, libusb):
    backend = usb.backend.libusb1.get_backend(find_library=lambda _: str(libusb))
    if backend is None:
        raise OSError('Could not load the HX Edit libusb library.')
    devices = [device for device in usb.core.find(find_all=True, idVendor=0x0E41, backend=backend)
               if device.idProduct in PRODUCT_IDS]
    if not devices:
        return {}
    if len(devices) != 1:
        raise OSError('Connect one Helix to scan NAM profiles.')
    device = devices[0]
    try:
        link = UsbLink(device)
        usb.util.claim_interface(device, 0)
        return scan_link(firmware_tools, link)
    finally:
        usb.util.dispose_resources(device)
