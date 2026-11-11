"""Coordinator for the Petkit BLE integration.

One supervisor task owns the whole connection life cycle:

    connect -> handshake -> poll ... poll -> (link lost) -> release -> back off -> connect

There is exactly one place that reconnects. Nothing is spawned per refresh,
nothing is retried from inside another retry loop, and every abandoned BLE
client is disconnected (see ha_bluetooth_adapter.py for why that matters).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDRESS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    STALE_AFTER_SECONDS,
)
from .ha_bluetooth_adapter import HABluetoothAdapter
from .PetkitW5BLEMQTT.commands import Commands
from .PetkitW5BLEMQTT.device import Device
from .PetkitW5BLEMQTT.event_handlers import EventHandlers
from .visits import STORAGE_VERSION, VisitTracker

_LOGGER = logging.getLogger(__name__)

# After the link drops, give the fountain a moment to start advertising again.
MIN_RECONNECT_DELAY = 2.0
MAX_RECONNECT_DELAY = 60.0
# Declare the session dead after this many consecutive polls without any answer.
MAX_SILENT_POLLS = 3
# Read the battery every N polls (it changes slowly).
BATTERY_EVERY_N_POLLS = 30
# While a pet is at the bowl, poll this often so the end of the visit is seen promptly.
VISIT_POLL_INTERVAL = 3


class _NotifyingEventHandlers:
    """Wraps EventHandlers so entities refresh when a frame has been parsed."""

    def __init__(self, inner: EventHandlers, on_frame) -> None:
        self._inner = inner
        self._on_frame = on_frame

    async def handle_notification(self, sender, message):
        result = await self._inner.handle_notification(sender, message)
        self._on_frame()
        return result


class PetkitBLECoordinator(DataUpdateCoordinator[None]):
    """Push-style coordinator: state is updated from BLE notifications."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,  # we poll ourselves, see _supervisor()
        )
        self.entry = entry
        # Bluetooth addresses are case sensitive in HA's stack. Always upper case.
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self.poll_interval: int = entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )

        self.device = Device(self.address)
        self.commands = Commands(ble_manager=None, device=self.device, logger=_LOGGER)
        self.commands.mac = self.address
        self._event_handlers = EventHandlers(
            device=self.device, commands=self.commands, logger=_LOGGER
        )
        self.adapter = HABluetoothAdapter(
            hass=hass,
            address=self.address,
            event_handler=_NotifyingEventHandlers(
                self._event_handlers, self._on_frame_processed
            ),
            logger=_LOGGER,
            on_disconnect=self._on_link_lost,
        )
        self.commands.ble_manager = self.adapter
        self.device.set_ble_manager(self.adapter)

        self._supervisor_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        self._unsub_advertisements = None
        self._unsub_stop = None
        self._unsub_midnight = None

        # Visits at the bowl, derived from the CTW3 pet-detected flag (see visits.py).
        self.visits = VisitTracker(
            Store(hass, STORAGE_VERSION, f"{DOMAIN}_visits_{entry.entry_id}")
        )

        self._wake = asyncio.Event()  # poll now / link lost / shutting down
        self._advertised = asyncio.Event()  # fountain advertisement seen
        self._stopping = False
        self._session_ready = False
        self._last_data = 0.0  # monotonic time of the last parsed frame
        self._poll_count = 0
        self._silent_polls = 0
        self._last_service_info: bluetooth.BluetoothServiceInfoBleak | None = None
        self._model_identified = False
        self.last_session_error: str | None = None

    # ------------------------------------------------------------------
    # Life cycle
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Start the background tasks. Returns immediately."""
        if self._supervisor_task is not None:
            return
        self._stopping = False
        await self.visits.async_load()
        # The "today" and "this month" figures roll over at midnight even if nothing is received.
        self._unsub_midnight = async_track_time_change(
            self.hass, self._on_midnight, hour=0, minute=0, second=2
        )
        self._unsub_advertisements = bluetooth.async_register_callback(
            self.hass,
            self._on_advertisement,
            bluetooth.BluetoothCallbackMatcher(
                address=self.address, connectable=True
            ),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        # Home Assistant does NOT unload integrations when it stops or restarts.
        # Without this the BLE link stays open inside BlueZ across the restart,
        # the fountain keeps its connection and stops advertising.
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_on_ha_stop
        )
        self._consumer_task = self.hass.async_create_background_task(
            self.adapter.message_consumer(), f"{DOMAIN} command writer {self.address}"
        )
        self._supervisor_task = self.hass.async_create_background_task(
            self._supervisor(), f"{DOMAIN} supervisor {self.address}"
        )

    async def _async_on_ha_stop(self, event: Event) -> None:
        """Home Assistant is stopping: release the fountain before the process exits."""
        self._unsub_stop = None
        await self.async_shutdown()

    async def async_shutdown(self) -> None:
        """Stop everything and release the fountain. Safe to call twice."""
        if getattr(self, "_unsub_stop", None) is not None:
            self._unsub_stop()
            self._unsub_stop = None
        self._stopping = True
        self._wake.set()
        self._advertised.set()
        if self._unsub_midnight is not None:
            self._unsub_midnight()
            self._unsub_midnight = None
        # A visit that is still open is ended at the last time it was seen, then saved.
        self.visits.close_open(interrupted=True)
        await self.visits.async_save_now()
        if self._unsub_advertisements is not None:
            self._unsub_advertisements()
            self._unsub_advertisements = None
        for task in (self._supervisor_task, self._consumer_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._supervisor_task, self._consumer_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Task ended with an error during shutdown", exc_info=True)
        self._supervisor_task = None
        self._consumer_task = None
        self._session_ready = False
        try:
            # The step the original code never reached: always release the link.
            await self.adapter.disconnect()
        finally:
            await super().async_shutdown()

    async def async_options_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Handle options update (takes effect on the next poll)."""
        new_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        if new_interval != self.poll_interval:
            _LOGGER.info("Poll interval changed from %s to %s s", self.poll_interval, new_interval)
            self.poll_interval = new_interval
            self._wake.set()

    # ------------------------------------------------------------------
    # Callbacks from the adapter / Bluetooth stack
    # ------------------------------------------------------------------
    @callback
    def _on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self._last_service_info = service_info
        self.device.status = {"rssi": service_info.rssi}
        self._advertised.set()

    @callback
    def _on_link_lost(self) -> None:
        self._session_ready = False
        # We cannot know what the flag does while out of reach: end any open visit now.
        self.visits.close_open(interrupted=True)
        self._wake.set()
        self.async_update_listeners()

    @callback
    def _on_frame_processed(self) -> None:
        self._last_data = time.monotonic()
        if self.device.alias == "CTW3":
            # Only the CTW3 reports the flag (other models leave it at its default).
            detected = bool(self.device.status.get("pet_detected"))
            if self.visits.update(detected, dt_util.now()):
                # Poll quickly right away so the end of the visit is not missed.
                self._wake.set()
        self.async_update_listeners()

    @callback
    def _on_midnight(self, now) -> None:
        """Day (and month) rolled over: refresh the today / this month sensors."""
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Supervisor
    # ------------------------------------------------------------------
    async def _supervisor(self) -> None:
        failures = 0
        while not self._stopping:
            try:
                if not (self.adapter.is_connected and self._session_ready):
                    if await self._establish_session():
                        failures = 0
                    else:
                        failures += 1
                        await self._wait_before_retry(failures)
                        continue

                # Connected and handshaken: sleep until the next poll, a
                # manual refresh request or a lost link, whichever is first.
                # A visit in progress is polled faster so its end is seen promptly.
                interval = self.poll_interval
                if self.visits.in_progress:
                    interval = min(interval, VISIT_POLL_INTERVAL)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                # Clear only after waking, so a request that arrives while we
                # are polling is not lost.
                self._wake.clear()
                if self._stopping:
                    break
                if not self.adapter.is_connected:
                    self._session_ready = False
                    await asyncio.sleep(MIN_RECONNECT_DELAY)
                    continue
                await self._poll()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the supervisor must never die
                _LOGGER.exception("Unexpected error in Petkit supervisor, continuing")
                await self.adapter.disconnect()
                self._session_ready = False
                await asyncio.sleep(5)

    async def _establish_session(self) -> bool:
        """Connect and run the handshake. Leaves nothing connected on failure."""
        self._session_ready = False
        if not await self.adapter.connect():
            self.last_session_error = self.adapter.connection_error
            self.async_update_listeners()
            return False
        try:
            self._identify_model()
            await self.commands.init_session()
        except asyncio.CancelledError:
            await self.adapter.disconnect()
            raise
        except Exception as err:  # noqa: BLE001
            self.last_session_error = f"Handshake failed: {type(err).__name__}: {err}"
            # Include the last raw notifications so a failure can be diagnosed from the log alone.
            _LOGGER.warning(
                "%s (will retry). GATT: %s. Last write error: %s. Last raw notifications: %s",
                self.last_session_error,
                self.adapter.char_info,
                self.adapter.last_write_error,
                [raw for _, raw in list(self.adapter.recent_raw)[-6:]] or "none received",
            )
            await self.adapter.disconnect()
            self.async_update_listeners()
            return False
        if not self.adapter.is_connected:
            self.last_session_error = "Link dropped during handshake"
            _LOGGER.warning("%s (will retry)", self.last_session_error)
            return False

        self.last_session_error = None
        self._session_ready = True
        self._silent_polls = 0
        self._poll_count = 0
        self._last_data = time.monotonic()
        _LOGGER.info(
            "Petkit session ready (%s, firmware %s)",
            self.device.product_name,
            self.device.firmware,
        )
        self._update_device_registry()
        self.async_update_listeners()
        return True

    async def _wait_before_retry(self, failures: int) -> None:
        """Back off. If the fountain is not visible at all, wait for its advertisement."""
        delay = min(MIN_RECONNECT_DELAY * (2 ** (failures - 1)), MAX_RECONNECT_DELAY)
        if self.adapter.ble_device_present():
            # Visible but the connection failed: classic exponential back-off.
            await asyncio.sleep(delay)
            return
        # Not visible: an advertisement is the best possible reason to retry.
        self._advertised.clear()
        try:
            await asyncio.wait_for(self._advertised.wait(), timeout=MAX_RECONNECT_DELAY)
        except asyncio.TimeoutError:
            return
        await asyncio.sleep(MIN_RECONNECT_DELAY)

    async def _poll(self) -> None:
        """Ask the fountain for fresh data and watch for a silent link."""
        started = time.monotonic()
        self._poll_count += 1
        await self.commands.get_device_state()
        await asyncio.sleep(0.4)
        await self.commands.get_device_update()
        await asyncio.sleep(0.6)
        if self._poll_count % BATTERY_EVERY_N_POLLS == 0:
            await self.commands.get_battery()
            await asyncio.sleep(0.4)

        if not self.adapter.is_connected:
            return
        if self.adapter.last_rx < started:
            # Writes succeed but nothing comes back. That is a half-open link.
            self._silent_polls += 1
            _LOGGER.debug("Poll got no answer (%d/%d)", self._silent_polls, MAX_SILENT_POLLS)
            if self._silent_polls >= MAX_SILENT_POLLS:
                _LOGGER.warning("Fountain stopped answering, resetting the connection")
                self.last_session_error = "Fountain stopped answering"
                await self.adapter.disconnect()
                self._session_ready = False
                self.async_update_listeners()
        else:
            self._silent_polls = 0

    # ------------------------------------------------------------------
    # Device identification
    # ------------------------------------------------------------------
    def _identify_model(self) -> None:
        """Work out the model from the advertisement (once per start)."""
        if self._model_identified:
            return
        info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        ) or self._last_service_info
        service_data = dict(info.service_data) if info and info.service_data else None
        manufacturer_data = dict(info.manufacturer_data) if info and info.manufacturer_data else None
        local_name = getattr(info, "name", None) if info else None
        self.commands.init_device_data(
            service_data=service_data,
            rssi=info.rssi if info else None,
            manufacturer_data=manufacturer_data,
            local_name=local_name,
        )
        # Identified when the name or any advertised payload gave a known model.
        if not str(self.device.alias).startswith(("UNKNOWN", "Uninitialized")):
            self._model_identified = True
        # Warning level on purpose: this decides how every frame is decoded, so it
        # must be visible in the default log.
        _LOGGER.warning(
            "Identified %s (%s) from name=%s service_data=%s manufacturer_data=%s",
            self.device.product_name,
            self.device.alias,
            local_name,
            {str(k): bytes(v).hex() for k, v in (service_data or {}).items()} or None,
            {str(k): bytes(v).hex() for k, v in (manufacturer_data or {}).items()} or None,
        )

    @callback
    def _update_device_registry(self) -> None:
        """Push the real model / firmware into the device registry (cosmetic)."""
        try:
            registry = dr.async_get(self.hass)
            device = registry.async_get_device(identifiers={(DOMAIN, self.address)})
            if device is None:
                return
            updates: dict[str, Any] = {}
            if self.device.product_name not in (None, "", "Uninitialized"):
                updates["model"] = self.device.product_name
            if self.device.firmware:
                updates["sw_version"] = str(self.device.firmware)
            if updates:
                registry.async_update_device(device.id, **updates)
        except Exception:  # noqa: BLE001 - must never break the connection
            _LOGGER.debug("Could not update the device registry", exc_info=True)

    # ------------------------------------------------------------------
    # Entity-facing API
    # ------------------------------------------------------------------
    @property
    def device_info(self) -> DeviceInfo:
        """Stable device info. The identifier never changes between restarts."""
        model = self.device.product_name
        if model in (None, "", "Uninitialized"):
            model = "Petkit Water Fountain"
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name="Water Fountain",
            manufacturer="Petkit",
            model=model,
            sw_version=str(self.device.firmware) if self.device.firmware else None,
        )

    @property
    def is_available(self) -> bool:
        """Connected, or data was received recently (rides out short reconnects)."""
        if self.adapter.is_connected and self._session_ready:
            return True
        return (
            self._last_data > 0
            and (time.monotonic() - self._last_data) < STALE_AFTER_SECONDS
        )

    @property
    def current_data(self) -> dict[str, Any]:
        """Return the current device data for entities."""
        return {
            "status": self.device.status,
            "config": self.device.config,
            "info": self.device.info,
            "name": self.device.name_readable,
            "product_name": self.device.product_name,
            "firmware": self.device.firmware,
            "serial": self.device.serial,
        }

    def _require_connection(self) -> None:
        if not (self.adapter.is_connected and self._session_ready):
            raise HomeAssistantError(
                "The Petkit fountain is not connected right now. It will be retried "
                "automatically; check the Connection sensor."
            )

    async def async_request_refresh(self) -> None:
        """Poll as soon as possible (used after a command was sent)."""
        self._wake.set()

    async def async_set_device_mode(self, state: int, mode: int) -> None:
        """Set device mode (power and operation mode)."""
        self._require_connection()
        await self.commands.set_device_mode(state, mode)

    async def async_reset_filter(self) -> None:
        """Reset the device filter."""
        self._require_connection()
        await self.commands.set_reset_filter()

    async def async_set_device_config(self, config_data: list) -> None:
        """Set device configuration."""
        self._require_connection()
        if self.device.alias == "CTW3":
            # The CTW3 uses a different 12 byte layout; sending the W5 layout
            # could overwrite settings with wrong values.
            raise HomeAssistantError("Changing the configuration is not yet supported for the CTW3")
        await self.commands.set_device_config(config_data)

    async def _async_update_data(self) -> None:
        """Not used: data is pushed from notifications."""
        return None
