"""Petkit BLE Water Fountain integration for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import CONF_ADDRESS, DOMAIN
from .coordinator import PetkitBLECoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BINARY_SENSOR]

# Service schemas
SERVICE_RESET_FILTER = "reset_filter"
SERVICE_SET_DEVICE_CONFIG = "set_device_config"

SERVICE_RESET_FILTER_SCHEMA = vol.Schema({})

SERVICE_SET_DEVICE_CONFIG_SCHEMA = vol.Schema({
    vol.Optional("smart_time_on"): cv.positive_int,
    vol.Optional("smart_time_off"): cv.positive_int,
    vol.Optional("led_brightness", default=80): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
})


def _coordinators(hass: HomeAssistant) -> list[PetkitBLECoordinator]:
    return list(hass.data.get(DOMAIN, {}).values())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Petkit BLE from a config entry."""
    # Home Assistant's Bluetooth stack looks devices up by UPPER-case address.
    # Older versions stored whatever the user typed, which made a lower-case
    # address "not found". Normalise it once.
    address = entry.data.get(CONF_ADDRESS, "")
    if address and address != address.upper():
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: address.upper()}
        )

    coordinator = PetkitBLECoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # entry.async_on_unload gives us a proper unsubscribe. (The original code
    # looked for ConfigEntry.async_remove_update_listener, which does not exist,
    # and crashed on every unload.)
    entry.async_on_unload(entry.add_update_listener(coordinator.async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Non-blocking: starts the connection supervisor in the background.
    await coordinator.async_start()

    # Register services (once, not per entry)
    if not hass.services.has_service(DOMAIN, SERVICE_RESET_FILTER):

        async def handle_reset_filter(call: ServiceCall) -> None:
            """Handle the reset filter service call."""
            for coordinator in _coordinators(hass):
                await coordinator.async_reset_filter()
                await coordinator.async_request_refresh()

        async def handle_set_device_config(call: ServiceCall) -> None:
            """Handle the set device config service call."""
            for coordinator in _coordinators(hass):
                current_config = coordinator.device.config

                smart_time_on = call.data.get("smart_time_on", current_config.get("smart_time_on", 30))
                smart_time_off = call.data.get("smart_time_off", current_config.get("smart_time_off", 60))
                led_brightness = call.data.get("led_brightness", current_config.get("led_brightness", 80))

                # Configuration array in the order the device expects.
                config_data = [
                    smart_time_on,
                    smart_time_off,
                    current_config.get("led_switch", 1),
                    led_brightness,
                    current_config.get("led_on_byte1", 0),
                    current_config.get("led_on_byte2", 0),
                    current_config.get("led_off_byte1", 0),
                    current_config.get("led_off_byte2", 0),
                    current_config.get("do_not_disturb_switch", 0),
                    current_config.get("dnd_on_byte1", 0),
                    current_config.get("dnd_on_byte2", 0),
                    current_config.get("dnd_off_byte1", 0),
                    current_config.get("dnd_off_byte2", 0),
                    current_config.get("is_locked", 0),
                ]

                await coordinator.async_set_device_config(config_data)
                await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_FILTER,
            handle_reset_filter,
            schema=SERVICE_RESET_FILTER_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_DEVICE_CONFIG,
            handle_set_device_config,
            schema=SERVICE_SET_DEVICE_CONFIG_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: PetkitBLECoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

        # Remove services if no more entries
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RESET_FILTER)
            hass.services.async_remove(DOMAIN, SERVICE_SET_DEVICE_CONFIG)

    return unload_ok
