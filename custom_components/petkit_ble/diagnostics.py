"""Diagnostics for the Petkit BLE integration.

Download from Settings > Devices & services > Petkit BLE > (device) > Download
diagnostics. The raw frames make it possible to decode a fountain model whose
protocol is not fully known yet (for example the CTW3).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"address", "mac", "mac_readable", "serial", "device_id", "device_id_bytes"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    adapter = coordinator.adapter

    service_info = bluetooth.async_last_service_info(
        hass, coordinator.address, connectable=True
    )
    advertisement = None
    if service_info is not None:
        advertisement = {
            "name": service_info.name,
            "rssi": service_info.rssi,
            "source": service_info.source,
            "service_uuids": list(service_info.service_uuids),
            "service_data": {k: bytes(v).hex() for k, v in service_info.service_data.items()},
            "manufacturer_data": {
                str(k): bytes(v).hex() for k, v in service_info.manufacturer_data.items()
            },
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "connection": {
            "status": adapter.connection_status,
            "is_connected": adapter.is_connected,
            "session_ready": coordinator._session_ready,  # noqa: SLF001
            "visible_to_bluetooth": adapter.ble_device_present(),
            "attempts_since_last_success": adapter.connection_attempts,
            "last_error": adapter.connection_error,
            "last_session_error": coordinator.last_session_error,
            "last_seen": adapter.last_seen,
            "gatt": adapter.char_info,
            "last_write_error": adapter.last_write_error,
            "connects": adapter.connect_count,
            "disconnects": adapter.disconnect_count,
            "frame_parse_errors": adapter.parse_errors,
        },
        "advertisement": advertisement,
        "device": {
            "info": async_redact_data(coordinator.device.info, TO_REDACT),
            "status": async_redact_data(coordinator.device.status, TO_REDACT),
            "config": coordinator.device.config,
        },
        # Latest complete frame per command id, as hex. Handy for decoding.
        "last_frames_by_cmd": {str(k): v for k, v in adapter.last_frames.items()},
        # Last raw notification fragments exactly as received.
        "recent_raw_notifications": list(adapter.recent_raw),
    }
