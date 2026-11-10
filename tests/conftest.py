"""Test harness: real Home Assistant core objects, fake Bluetooth radio."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_fountain import FakeFountain  # noqa: E402

ADDRESS = "A4:C1:38:A5:76:82"
# A real CTW3 broadcasts its model byte as MANUFACTURER data (company id 0xC1A4), not as service data.
CTW3_MANUFACTURER_DATA = {0xC1A4: bytes([0x38, 0xA5, 0x76, 0x82, 0x00, 0xF7])}


class FastAsyncio:
    """The asyncio module, but sleep() runs 50x faster (tests would take minutes otherwise)."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(delay, *args, **kwargs):
        await asyncio.sleep(delay / 50)


@pytest.fixture
async def hass(tmp_path):
    from homeassistant.core import HomeAssistant, CoreState

    hass = HomeAssistant(str(tmp_path))
    hass.set_state(CoreState.running)
    yield hass
    await hass.async_stop(force=True)


@pytest.fixture
def fountain(monkeypatch):
    """Patch the Bluetooth layer so the integration talks to a FakeFountain."""
    from homeassistant.components import bluetooth

    f = FakeFountain()
    callbacks: list = []
    f.adv_callbacks = callbacks

    monkeypatch.setattr(
        "custom_components.petkit_ble.ha_bluetooth_adapter.establish_connection",
        f.establish_connection,
    )
    monkeypatch.setattr(
        bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable=True: object() if f.visible else None,
    )
    monkeypatch.setattr(
        bluetooth,
        "async_last_service_info",
        lambda hass, address, connectable=True: SimpleNamespace(
            name="Petkit_CTW3_100",
            rssi=-62,
            source="hci0",
            service_data={},
            service_uuids=[],
            manufacturer_data=CTW3_MANUFACTURER_DATA,
        ),
    )

    f.stale_release_calls = 0

    async def fake_release(self, rate_limited=False):
        f.stale_release_calls += 1

    monkeypatch.setattr(
        "custom_components.petkit_ble.ha_bluetooth_adapter.HABluetoothAdapter._release_stale_bluez_link",
        fake_release,
    )

    async def fake_probe(self):
        return "hci0: Connected=True ServicesResolved=False"

    monkeypatch.setattr(
        "custom_components.petkit_ble.ha_bluetooth_adapter.HABluetoothAdapter._probe_bluez_state",
        fake_probe,
    )

    def register(hass, cb, matcher, mode):
        callbacks.append(cb)
        return lambda: callbacks.remove(cb) if cb in callbacks else None

    monkeypatch.setattr(bluetooth, "async_register_callback", register)

    # Speed up the sleeps in the protocol layer and the supervisor.
    monkeypatch.setattr("custom_components.petkit_ble.PetkitW5BLEMQTT.commands.asyncio", FastAsyncio())
    monkeypatch.setattr("custom_components.petkit_ble.coordinator.asyncio", FastAsyncio())
    monkeypatch.setattr("custom_components.petkit_ble.coordinator.MIN_RECONNECT_DELAY", 0.02)
    monkeypatch.setattr("custom_components.petkit_ble.coordinator.MAX_RECONNECT_DELAY", 0.2)
    return f


@pytest.fixture
def make_coordinator(hass, fountain):
    created = []

    async def _make(address=ADDRESS, poll_interval=0.2):
        from custom_components.petkit_ble.coordinator import PetkitBLECoordinator

        entry = SimpleNamespace(
            entry_id="test-entry",
            data={"address": address},
            options={"update_interval": poll_interval},
            title="Petkit Water Fountain",
            domain="petkit_ble",
            async_on_unload=lambda cb: None,
        )
        coordinator = PetkitBLECoordinator(hass, entry)  # type: ignore[arg-type]
        created.append(coordinator)
        return coordinator

    yield _make


async def wait_until(predicate, timeout=5.0, interval=0.01):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())
