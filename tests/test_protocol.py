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


def test_ctw3_short_config_payload_does_not_raise():
    """Real CTW3 cmd 211 reply: 12 data bytes (the W5 layout has 14)."""
    from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers

    data = bytes.fromhex("030300190e1001020000 0000".replace(" ", ""))
    result = Parsers.device_configuration(data, "CTW3")
    assert result["smart_time_on"] == 3
    assert result["is_locked"] == 0


# Raw replies captured from a real Petkit CTW3 (cmd data only, without frame header)
CTW3_210 = bytes.fromhex("0101010100000000000000367b640100000053001569106964007f056705")
CTW3_230 = bytes.fromhex(
    "0101010100000000000000367b640100000053001569106964007f0567050303"
    "00190e10010200000000"
)


def test_ctw3_state_decodes_real_frame():
    from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers

    r = Parsers.device_state(CTW3_210, "CTW3")
    assert r["power_status"] == 1
    assert r["mode"] == 1
    assert r["warning_breakdown"] == 0
    assert r["warning_water_missing"] == 0
    assert r["warning_filter"] == 0
    assert r["pump_runtime"] == 13947
    assert r["pump_runtime_today"] == 83
    assert r["filter_percentage"] == 1.0
    assert r["running_status"] == 1
    assert r["battery"] == 100
    assert abs(r["voltage"] - 4.201) < 0.001
    # 83 s of pumping today is about a litre, not tens of millions
    assert 0.5 < r["purified_water_today"] < 2.0


def test_ctw3_full_status_includes_config_and_filter_days():
    from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers

    r = Parsers.device_status(CTW3_230, "CTW3")
    assert r["smart_time_on"] == 3 and r["smart_time_off"] == 3
    assert r["led_switch"] == 1 and r["led_brightness"] == 2
    assert r["is_locked"] == 0
    assert r["filter_time_left"] == 60  # normal mode, 100 %


def test_ctw3_transient_mode_zero_is_ignored():
    from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers

    frame = bytearray(CTW3_210)
    frame[2] = 0
    assert "mode" not in Parsers.device_state(bytes(frame), "CTW3")


def test_model_is_identified_from_name_when_advertisement_has_no_payload():
    from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils

    assert Utils.identifier_from_name("Petkit_CTW3_100") == 247
    assert Utils.identifier_from_name("petkit_ctw2") == 217
    assert Utils.identifier_from_name("Something_else") is None
    assert Utils.identifier_from_name(None) is None
