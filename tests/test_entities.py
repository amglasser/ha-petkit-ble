"""Entities, diagnostics and config flow wiring against real Home Assistant classes."""
from __future__ import annotations

from conftest import ADDRESS, wait_until


async def test_entities_keep_ids_and_follow_availability(make_coordinator, fountain):
    from custom_components.petkit_ble.binary_sensor import PetkitWaterMissingSensor
    from custom_components.petkit_ble.sensor import (
        PetkitBatteryLevelSensor,
        PetkitConnectionStatusSensor,
        PetkitFilterPercentageSensor,
    )
    from custom_components.petkit_ble.switch import PetkitPowerSwitch

    c = await make_coordinator()
    battery = PetkitBatteryLevelSensor(c)
    conn = PetkitConnectionStatusSensor(c)
    water = PetkitWaterMissingSensor(c)
    power = PetkitPowerSwitch(c)
    filt = PetkitFilterPercentageSensor(c)

    # unique ids are exactly what the original produced in practice (address, no colons)
    assert battery.unique_id == "A4C138A57682_battery"
    assert water.unique_id == "A4C138A57682_water_missing"
    assert power.unique_id == "A4C138A57682_power"
    assert battery.device_info["identifiers"] == {("petkit_ble", ADDRESS)}

    # Not connected yet: data entities unavailable, connection diagnostics stay available
    assert battery.available is False
    assert conn.available is True

    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        assert await wait_until(lambda: battery.native_value == 80)
        assert await wait_until(lambda: power.is_on is True and filt.native_value == 12.0)
        assert battery.available is True
        assert water.is_on is False
        assert power.is_on is True
        assert filt.native_value == 12.0  # byte 12 = 12 % of filter life remaining
        assert conn.native_value == "connected"
    finally:
        await c.async_shutdown()


async def test_diagnostics_contains_raw_frames(hass, make_coordinator, fountain):
    from custom_components.petkit_ble.diagnostics import async_get_config_entry_diagnostics

    c = await make_coordinator()
    hass.data.setdefault("petkit_ble", {})[c.entry.entry_id] = c
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        assert await wait_until(lambda: 230 in c.adapter.last_frames)
        diag = await async_get_config_entry_diagnostics(hass, c.entry)
        assert diag["connection"]["is_connected"] is True
        assert "230" in diag["last_frames_by_cmd"]
        assert diag["advertisement"]["manufacturer_data"]
        assert diag["entry"]["address"] == "**REDACTED**"
    finally:
        await c.async_shutdown()


def test_config_flow_module_and_options_flow_construct():
    from custom_components.petkit_ble.config_flow import OptionsFlowHandler, PetkitBLEConfigFlow

    # The original assigned the read-only config_entry in __init__; constructing must not raise
    OptionsFlowHandler()
    assert PetkitBLEConfigFlow.VERSION == 1


async def test_derived_sensors_are_labelled_as_estimates(make_coordinator, fountain):
    """Water and energy are calculated from pump runtime, not measured, and must say so.

    The unique ids stay the same, so existing entities and dashboards are unaffected.
    """
    from custom_components.petkit_ble.sensor import (
        PetkitEnergyConsumedSensor,
        PetkitPurifiedWaterSensor,
        PetkitPurifiedWaterTodaySensor,
    )

    c = await make_coordinator()
    total = PetkitPurifiedWaterSensor(c)
    today = PetkitPurifiedWaterTodaySensor(c)
    energy = PetkitEnergyConsumedSensor(c)

    assert total.name == "Total Water Purified (estimate)"
    assert today.name == "Water Purified Today (estimate)"
    assert energy.name == "Energy Consumption (estimate)"
    for sensor in (total, today, energy):
        assert sensor.extra_state_attributes["estimated"] is True
        assert sensor.extra_state_attributes["basis"]
    assert total.unique_id == "A4C138A57682_purified_water"
    assert today.unique_id == "A4C138A57682_purified_water_today"
    assert energy.unique_id == "A4C138A57682_energy_consumed"
