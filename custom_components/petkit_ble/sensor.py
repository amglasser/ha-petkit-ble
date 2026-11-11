"""Sensor platform for Petkit BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfEnergy, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from datetime import datetime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import PetkitBLECoordinator
from .visits import day_key, format_duration

# The last three visits, newest first.
VISIT_LABELS = ("Last Visit", "Previous Visit", "Third Last Visit")

VISIT_BASIS = (
    "pet-detected flag, sampled every few seconds while connected; "
    "short visits and visits while disconnected are missed"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Petkit BLE sensors."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        PetkitBatteryLevelSensor(coordinator),
        PetkitFilterPercentageSensor(coordinator),
        PetkitFilterTimeLeftSensor(coordinator),
        PetkitPumpRuntimeSensor(coordinator),
        PetkitPumpRuntimeTodaySensor(coordinator),
        PetkitPurifiedWaterSensor(coordinator),
        PetkitPurifiedWaterTodaySensor(coordinator),
        PetkitEnergyConsumedSensor(coordinator),
        PetkitRSSISensor(coordinator),
        PetkitVoltageSensor(coordinator),
        PetkitConnectionStatusSensor(coordinator),
        PetkitConnectionAttemptsSensor(coordinator),
        PetkitLastSeenSensor(coordinator),
        PetkitSupplyVoltageSensor(coordinator),
        PetkitVisitsTodaySensor(coordinator),
        PetkitDrinkTimeTodaySensor(coordinator),
        PetkitVisitsMonthSensor(coordinator),
        PetkitDrinkTimeMonthSensor(coordinator),
        PetkitVisitHistorySensor(coordinator),
    ]
    for index, label in enumerate(VISIT_LABELS):
        entities.append(PetkitVisitSensor(coordinator, index, label))
        entities.append(PetkitVisitDurationSensor(coordinator, index, label))
    
    async_add_entities(entities)

class PetkitSensorBase(CoordinatorEntity[PetkitBLECoordinator], SensorEntity):
    """Base class for Petkit sensors."""
    
    # Connection diagnostics stay available while the fountain is away, so you
    # can see WHY it is away. Everything else goes unavailable when stale.
    _always_available = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info (stable identifier, see coordinator)."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Entity availability follows the connection, not an update timer."""
        return self._always_available or self.coordinator.is_available

    @property
    def name(self) -> str:
        """Return dynamic entity name."""
        # Return the sensor-specific name template (which no longer includes device name)
        return getattr(self, '_sensor_name_template', 'Unknown Sensor')

    def _get_device_id(self) -> str:
        """Device id for unique_id generation: the address without colons.

        This is what the original code produced in practice (the serial was
        never known yet when entities were created), so existing entities keep
        their unique ids.
        """
        return self.coordinator.address.replace(":", "")

class PetkitBatteryLevelSensor(PetkitSensorBase):
    """Battery level sensor."""
    
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_battery"
        self._sensor_name_template = "Battery"
    
    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        return self.coordinator.current_data.get("status", {}).get("battery")

class PetkitFilterPercentageSensor(PetkitSensorBase):
    """Filter percentage sensor."""
    
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter percentage sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_filter_percentage"
        self._sensor_name_template = "Filter Remaining"
    
    @property
    def native_value(self) -> float | None:
        """Return the filter percentage remaining."""
        raw_value = self.coordinator.current_data.get("status", {}).get("filter_percentage")
        if raw_value is not None:
            # The parser delivers a FRACTION (0.0-1.0) of filter life REMAINING:
            # filter_time_left = ceil(fraction * 30 days * ...) uses it the same way.
            # (The original code computed 100 - fraction, which always showed ~99-100 %.)
            return round(raw_value * 100, 1)
        return None

class PetkitFilterTimeLeftSensor(PetkitSensorBase):
    """Filter time left sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-outline"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter time left sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_filter_time_left"
        self._sensor_name_template = "Filter Days Left"
    
    @property
    def native_value(self) -> int | None:
        """Return the filter time left in days."""
        return self.coordinator.current_data.get("status", {}).get("filter_time_left")

class PetkitPumpRuntimeSensor(PetkitSensorBase):
    """Pump runtime sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:pump"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the pump runtime sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_pump_runtime"
        self._sensor_name_template = "Pump Total Runtime"
    
    @property
    def native_value(self) -> float | None:
        """Return the pump runtime in hours."""
        readable = self.coordinator.current_data.get("status", {}).get("pump_runtime_readable")
        if readable and isinstance(readable, str):
            # Parse "3 days, 19 hours" or "5 hours" to numeric hours
            try:
                total_hours = 0
                if "day" in readable:
                    parts = readable.split(", ")
                    days_part = parts[0].split()[0]
                    total_hours += int(days_part) * 24
                    if len(parts) > 1:
                        hours_part = parts[1].split()[0]
                        total_hours += int(hours_part)
                elif "hour" in readable:
                    hours_part = readable.split()[0]
                    total_hours = int(hours_part)
                return round(float(total_hours), 1)
            except (ValueError, IndexError):
                return None
        # Handle numeric values directly (in seconds, convert to hours)
        raw_runtime = self.coordinator.current_data.get("status", {}).get("pump_runtime")
        if raw_runtime is not None and isinstance(raw_runtime, (int, float)):
            return round(float(raw_runtime) / 3600, 1)  # Convert seconds to hours
        return None

class PetkitPumpRuntimeTodaySensor(PetkitSensorBase):
    """Pump runtime today sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:pump"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the pump runtime today sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_pump_runtime_today"
        self._sensor_name_template = "Pump Today Runtime"
    
    @property
    def native_value(self) -> float | None:
        """Return the pump runtime today in hours."""
        readable = self.coordinator.current_data.get("status", {}).get("pump_runtime_today_readable")
        if readable and isinstance(readable, str):
            # Parse "5:50h" to numeric hours (5.83)
            try:
                if ":" in readable:
                    # Format like "5:50h"
                    time_part = readable.replace("h", "")
                    hours, minutes = time_part.split(":")
                    return round(float(hours) + float(minutes) / 60.0, 2)
                elif "h" in readable:
                    # Format like "5h"
                    hours_part = readable.replace("h", "")
                    return round(float(hours_part), 2)
                else:
                    # Try to parse as plain number
                    return round(float(readable), 2)
            except (ValueError, IndexError):
                return None
        # Handle numeric values directly (in seconds, convert to hours)
        raw_runtime = self.coordinator.current_data.get("status", {}).get("pump_runtime_today")
        if raw_runtime is not None and isinstance(raw_runtime, (int, float)):
            return round(float(raw_runtime) / 3600, 2)  # Convert seconds to hours
        return None

class PetkitPurifiedWaterSensor(PetkitSensorBase):
    """Purified water sensor."""
    
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:water"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the purified water sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_purified_water"
        self._sensor_name_template = "Total Water Purified (estimate)"
    
    @property
    def native_value(self) -> float | None:
        """Return the total purified water."""
        return self.coordinator.current_data.get("status", {}).get("purified_water")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say plainly that this value is calculated, not measured by the fountain."""
        return {"estimated": True, "basis": "pump runtime x assumed flow rate"}


class PetkitPurifiedWaterTodaySensor(PetkitSensorBase):
    """Purified water today sensor."""
    
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:water"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the purified water today sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_purified_water_today"
        self._sensor_name_template = "Water Purified Today (estimate)"
    
    @property
    def native_value(self) -> float | None:
        """Return today's purified water."""
        return self.coordinator.current_data.get("status", {}).get("purified_water_today")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say plainly that this value is calculated, not measured by the fountain."""
        return {"estimated": True, "basis": "pump runtime x assumed flow rate"}


class PetkitEnergyConsumedSensor(PetkitSensorBase):
    """Energy consumed sensor."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the energy consumed sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_energy_consumed"
        self._sensor_name_template = "Energy Consumption (estimate)"
    
    @property
    def native_value(self) -> float | None:
        """Return the energy consumed."""
        return self.coordinator.current_data.get("status", {}).get("energy_consumed")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say plainly that this value is calculated, not measured by the fountain."""
        return {"estimated": True, "basis": "pump runtime x assumed power draw"}


class PetkitRSSISensor(PetkitSensorBase):
    """RSSI sensor."""
    
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the RSSI sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_rssi"
        self._sensor_name_template = "Signal Strength"
    
    @property
    def native_value(self) -> int | None:
        """Return the RSSI value."""
        return self.coordinator.current_data.get("status", {}).get("rssi")

class PetkitVoltageSensor(PetkitSensorBase):
    """Voltage sensor."""
    
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "V"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the voltage sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_voltage"
        self._sensor_name_template = "Voltage"
    
    @property
    def native_value(self) -> float | None:
        """Return the voltage."""
        return self.coordinator.current_data.get("status", {}).get("voltage")

class PetkitConnectionStatusSensor(PetkitSensorBase):
    """Connection status sensor."""
    
    _attr_icon = "mdi:bluetooth-connect"
    _always_available = True

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the connection status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_connection_status"
        self._sensor_name_template = "Connection"
    
    @property
    def native_value(self) -> str | None:
        """Return the connection status."""
        return self.coordinator.current_data.get("status", {}).get("connection_status", "unknown")

class PetkitConnectionAttemptsSensor(PetkitSensorBase):
    """Connection attempts sensor."""
    
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    _always_available = True
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the connection attempts sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_connection_attempts"
        self._sensor_name_template = "Connection Attempts"
    
    @property
    def native_value(self) -> int | None:
        """Return the number of connection attempts."""
        return self.coordinator.current_data.get("status", {}).get("connection_attempts", 0)

class PetkitLastSeenSensor(PetkitSensorBase):
    """Last seen sensor."""
    
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_registry_enabled_default = False
    _always_available = True
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the last seen sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_last_seen"
        self._sensor_name_template = "Last Seen"
    
    @property
    def native_value(self) -> datetime | None:
        """Return the last seen timestamp."""
        last_seen = self.coordinator.current_data.get("status", {}).get("last_seen")
        if last_seen:
            # Handle both timestamp (float) and ISO string formats for backward compatibility
            if isinstance(last_seen, str):
                try:
                    from datetime import datetime as dt
                    # Parse ISO format string to datetime
                    return dt.fromisoformat(last_seen.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    return None
            else:
                # Legacy numeric timestamp format
                return datetime.fromtimestamp(last_seen)
        return None

class PetkitSupplyVoltageSensor(PetkitSensorBase):
    """Voltage of the power adapter (CTW3 only, bytes 20-21 of the state frame)."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_supply_voltage"
        self._sensor_name_template = "Supply Voltage"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.device.alias != "CTW3":
            return None
        return self.coordinator.current_data.get("status", {}).get("supply_voltage")


class PetkitVisitSensorBase(PetkitSensorBase):
    """Visit statistics are kept by Home Assistant, so they stay available while the fountain is away."""

    _always_available = True
    _attr_icon = "mdi:cat"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"estimated": True, "basis": VISIT_BASIS}


class PetkitVisitsTodaySensor(PetkitVisitSensorBase):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_visits_today"
        self._sensor_name_template = "Visits Today (estimate)"

    @property
    def native_value(self) -> int:
        return self.coordinator.visits.today(dt_util.now())["visits"]


class PetkitDrinkTimeTodaySensor(PetkitVisitSensorBase):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_drink_time_today"
        self._sensor_name_template = "Time at Bowl Today (estimate)"

    @property
    def native_value(self) -> int:
        return self.coordinator.visits.today(dt_util.now())["seconds"]


class PetkitVisitsMonthSensor(PetkitVisitSensorBase):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_visits_month"
        self._sensor_name_template = "Visits This Month (estimate)"

    @property
    def native_value(self) -> int:
        return self.coordinator.visits.this_month(dt_util.now())["visits"]


class PetkitDrinkTimeMonthSensor(PetkitVisitSensorBase):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_drink_time_month"
        self._sensor_name_template = "Time at Bowl This Month (estimate)"

    @property
    def native_value(self) -> int:
        return self.coordinator.visits.this_month(dt_util.now())["seconds"]


class PetkitVisitSensor(PetkitVisitSensorBase):
    """When one of the last three visits started (timestamp), with its details as attributes."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: PetkitBLECoordinator, index: int, label: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{self._get_device_id()}_visit_{index + 1}"
        self._sensor_name_template = label

    @property
    def native_value(self) -> datetime | None:
        visit = self.coordinator.visits.visit(self._index)
        return dt_util.parse_datetime(visit["start"]) if visit else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        visit = self.coordinator.visits.visit(self._index)
        if visit:
            attrs.update(
                ended=visit["end"],
                duration_seconds=visit["seconds"],
                duration=format_duration(visit["seconds"]),
                interrupted=visit["interrupted"],
            )
        return attrs


class PetkitVisitDurationSensor(PetkitVisitSensorBase):
    """How long one of the last three visits lasted."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: PetkitBLECoordinator, index: int, label: str) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{self._get_device_id()}_visit_{index + 1}_duration"
        self._sensor_name_template = f"{label} Duration"

    @property
    def native_value(self) -> int | None:
        visit = self.coordinator.visits.visit(self._index)
        return visit["seconds"] if visit else None


class PetkitVisitHistorySensor(PetkitVisitSensorBase):
    """All recorded history: daily for a rolling year, monthly forever (as attributes)."""

    # Large and only useful to dashboards: keep them out of the recorder database.
    _unrecorded_attributes = frozenset({"daily", "monthly"})

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_visit_history"
        self._sensor_name_template = "Visit History (estimate)"

    @property
    def native_value(self) -> int:
        """Total visits recorded since tracking started."""
        return self.coordinator.visits.total_visits

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        visits = self.coordinator.visits
        attrs = super().extra_state_attributes
        attrs.update(
            today=day_key(dt_util.now()),
            days_recorded=len(visits.daily),
            months_recorded=len(visits.monthly),
            daily=visits.daily,
            monthly=visits.monthly,
        )
        return attrs
