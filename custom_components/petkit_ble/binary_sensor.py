"""Binary sensor platform for Petkit BLE integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import PetkitBLECoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Petkit BLE binary sensors."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        PetkitFilterProblemSensor(coordinator),
        PetkitWaterMissingSensor(coordinator),
        PetkitBreakdownSensor(coordinator),
        PetkitRunningSensor(coordinator),
        PetkitPetDetectedSensor(coordinator),
        PetkitACPowerSensor(coordinator),
        PetkitLowBatterySensor(coordinator),
    ]
    
    async_add_entities(entities)

class PetkitBinarySensorBase(CoordinatorEntity[PetkitBLECoordinator], BinarySensorEntity):
    """Base class for Petkit binary sensors."""
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        # Use MAC address as fallback if serial not available
        # Device info will be provided by the dynamic property
    
    @property
    def device_info(self) -> DeviceInfo:
        """Return device info (stable identifier, see coordinator)."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Entity availability follows the connection, not an update timer."""
        return self.coordinator.is_available

class PetkitFilterProblemSensor(PetkitBinarySensorBase):
    """Filter problem binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter problem sensor."""
        super().__init__(coordinator)
        device_id = coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_filter_problem"
        self._attr_name = "Filter Problem"
        self._attr_icon = "mdi:air-filter"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if there's a filter problem."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_filter")
        return bool(warning) if warning is not None else None

class PetkitWaterMissingSensor(PetkitBinarySensorBase):
    """Water missing binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the water missing sensor."""
        super().__init__(coordinator)
        device_id = coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_water_missing"
        self._attr_name = "Water Missing"
        self._attr_icon = "mdi:water-alert"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if water is missing."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_water_missing")
        return bool(warning) if warning is not None else None

class PetkitBreakdownSensor(PetkitBinarySensorBase):
    """Breakdown binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the breakdown sensor."""
        super().__init__(coordinator)
        device_id = coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_breakdown"
        self._attr_name = "Breakdown"
        self._attr_icon = "mdi:alert-circle"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if there's a breakdown."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_breakdown")
        return bool(warning) if warning is not None else None

class PetkitRunningSensor(PetkitBinarySensorBase):
    """Running status binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the running sensor."""
        super().__init__(coordinator)
        device_id = coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_running"
        self._attr_name = "Running"
        self._attr_icon = "mdi:play-circle"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if the fountain is running."""
        running_status = self.coordinator.current_data.get("status", {}).get("running_status")
        return bool(running_status) if running_status is not None else None

class PetkitCTW3BinarySensor(PetkitBinarySensorBase):
    """These flags only exist in the CTW3 state frame; other models show unknown."""

    @property
    def _is_ctw3(self) -> bool:
        return self.coordinator.device.alias == "CTW3"

    def _status(self, key: str):
        return self.coordinator.current_data.get("status", {}).get(key) if self._is_ctw3 else None


class PetkitPetDetectedSensor(PetkitCTW3BinarySensor):
    """A pet is at the bowl right now (state frame byte 19)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:cat"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.replace(':', '')}_pet_detected"
        self._attr_name = "Pet at Bowl"

    @property
    def is_on(self) -> bool | None:
        value = self._status("pet_detected")
        return bool(value) if value is not None else None


class PetkitACPowerSensor(PetkitCTW3BinarySensor):
    """Running from the mains adapter (state frame byte 3, value 2 = AC)."""

    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.replace(':', '')}_ac_power"
        self._attr_name = "AC Power"

    @property
    def is_on(self) -> bool | None:
        value = self._status("electric_status")
        return value == 2 if value is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        # The raw byte is shown so the mapping can be checked against the real fountain.
        return {"electric_status_raw": self._status("electric_status")}


class PetkitLowBatterySensor(PetkitCTW3BinarySensor):
    """The fountain reports its battery as low (state frame byte 7)."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address.replace(':', '')}_low_battery"
        self._attr_name = "Low Battery"

    @property
    def is_on(self) -> bool | None:
        value = self._status("low_battery")
        return bool(value) if value is not None else None
