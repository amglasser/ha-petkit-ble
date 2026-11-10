"""Pure protocol/library tests (no Home Assistant needed)."""
from __future__ import annotations

from custom_components.petkit_ble.PetkitW5BLEMQTT.commands import Commands
from custom_components.petkit_ble.PetkitW5BLEMQTT.device import Device
from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils


def test_ctw3_identifier_is_known():
    props = Utils.get_device_properties(247)
    assert props["alias"] == "CTW3"


def test_unknown_identifier_falls_back_instead_of_raising():
    props = Utils.get_device_properties(99)
    assert props["device_type"] == 14
    assert "99" in props["name"]


def test_filter_time_does_not_divide_by_zero():
    assert Utils.calculate_remaining_filter_time(0.5, 0, 0) >= 0


def test_init_device_data_from_ctw3_advertisement():
    device = Device("A4:C1:38:A5:76:82")
    cmds = Commands(ble_manager=None, device=device, logger=None)
    cmds.logger = type("L", (), {"debug": lambda *a, **k: None})()
    cmds.init_device_data(
        service_data={"x": bytes([0x38, 0xA5, 0x76, 0x82, 0x00, 0xF7])}, rssi=-62
    )
    assert device.alias == "CTW3"
    assert device.product_name == "Petkit CTW3"


def test_init_device_data_without_service_data_does_not_raise():
    device = Device("A4:C1:38:A5:76:82")
    cmds = Commands(ble_manager=None, device=device, logger=None)
    cmds.init_device_data(service_data=None, rssi=None)
    assert device.product_name == "Petkit Water Fountain"
