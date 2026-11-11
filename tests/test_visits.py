"""Visit tracking: unit tests for VisitTracker and the pet-detected / power entities end to end."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import wait_until

T0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)


class FakeStore:
    """Just enough of homeassistant.helpers.storage.Store."""

    def __init__(self, stored=None):
        self.stored = stored
        self.saved = None
        self.delayed = []

    async def async_load(self):
        return self.stored

    def async_delay_save(self, data_func, delay=0):
        self.delayed.append(data_func)

    async def async_save(self, data):
        self.saved = data


def make_tracker(stored=None):
    from custom_components.petkit_ble.visits import VisitTracker

    return VisitTracker(FakeStore(stored))


def test_visit_is_counted_at_start_and_timed_at_end():
    t = make_tracker()
    assert t.update(False, T0) is False  # nothing happening
    assert t.update(True, T0 + timedelta(seconds=10)) is True  # arrives
    assert t.in_progress
    assert t.today(T0) == {"visits": 1, "seconds": 0}  # counted immediately, time not yet known
    assert t.update(True, T0 + timedelta(seconds=13)) is False  # still drinking
    assert t.update(False, T0 + timedelta(seconds=40)) is True  # leaves
    assert not t.in_progress
    assert t.today(T0) == {"visits": 1, "seconds": 30}
    assert t.this_month(T0) == {"visits": 1, "seconds": 30}
    assert t.visit(0)["seconds"] == 30
    assert t.visit(0)["interrupted"] is False
    assert t.total_visits == 1


def test_only_the_last_three_visits_are_kept_newest_first():
    t = make_tracker()
    for i in range(5):
        start = T0 + timedelta(minutes=10 * i)
        t.update(True, start)
        t.update(False, start + timedelta(seconds=10 * (i + 1)))
    assert [v["seconds"] for v in t.last] == [50, 40, 30]
    assert t.visit(3) is None
    assert t.today(T0)["visits"] == 5
    assert t.today(T0)["seconds"] == 10 + 20 + 30 + 40 + 50


def test_link_loss_ends_the_open_visit_at_the_last_time_it_was_seen():
    t = make_tracker()
    t.update(True, T0)
    t.update(True, T0 + timedelta(seconds=12))
    assert t.close_open() is True
    assert not t.in_progress
    assert t.visit(0)["seconds"] == 12
    assert t.visit(0)["interrupted"] is True
    assert t.close_open() is False  # nothing left to close


def test_a_visit_belongs_to_the_day_and_month_it_started():
    t = make_tracker()
    late = datetime(2026, 11, 30, 23, 59, 50, tzinfo=timezone.utc)
    t.update(True, late)
    t.update(False, late + timedelta(seconds=30))  # ends in December
    assert t.daily == {"2026-11-30": {"visits": 1, "seconds": 30}}
    assert t.monthly == {"2026-11": {"visits": 1, "seconds": 30}}


def test_daily_figures_roll_over_a_year_but_monthly_are_kept():
    old_day = T0 - timedelta(days=366)
    edge_day = T0 - timedelta(days=365)
    t = make_tracker(
        {
            "daily": {
                old_day.date().isoformat(): {"visits": 4, "seconds": 100},
                edge_day.date().isoformat(): {"visits": 2, "seconds": 50},
            },
            "monthly": {"2025-01": {"visits": 90, "seconds": 3000}},
        }
    )
    import asyncio

    asyncio.run(t.async_load())
    t.update(True, T0)
    t.update(False, T0 + timedelta(seconds=5))
    assert old_day.date().isoformat() not in t.daily
    assert edge_day.date().isoformat() in t.daily  # still inside the rolling year
    assert t.monthly["2025-01"] == {"visits": 90, "seconds": 3000}  # never pruned
    assert t.monthly["2026-11"]["visits"] == 1


async def test_history_survives_a_restart_and_bad_files_are_ignored():
    from custom_components.petkit_ble.visits import VisitTracker

    store = FakeStore()
    t = VisitTracker(store)
    t.update(True, T0)
    t.update(False, T0 + timedelta(seconds=20))
    assert store.delayed  # a save was scheduled
    await t.async_save_now()

    restarted = VisitTracker(FakeStore(store.saved))
    await restarted.async_load()
    assert restarted.today(T0) == {"visits": 1, "seconds": 20}
    assert restarted.visit(0)["seconds"] == 20
    assert not restarted.in_progress

    for junk in (None, "text", {"daily": "x", "monthly": [1], "last": {"a": 1}},
                 {"daily": {"2026-11-11": {"visits": "many"}}}):
        broken = VisitTracker(FakeStore(junk))
        await broken.async_load()
        assert broken.daily == {} and broken.monthly == {} and broken.last == []


def test_format_duration():
    from custom_components.petkit_ble.visits import format_duration

    assert format_duration(None) is None
    assert format_duration(42) == "42 s"
    assert format_duration(185) == "3 min 5 s"
    assert format_duration(120) == "2 min"
    assert format_duration(3725) == "1 h 2 min"


# ----------------------------------------------------------------------
# End to end against the fake fountain
# ----------------------------------------------------------------------
async def test_pet_at_bowl_is_tracked_from_live_frames(make_coordinator, fountain, monkeypatch):
    from custom_components.petkit_ble.binary_sensor import PetkitPetDetectedSensor
    from custom_components.petkit_ble.sensor import (
        PetkitDrinkTimeTodaySensor,
        PetkitVisitDurationSensor,
        PetkitVisitSensor,
        PetkitVisitsMonthSensor,
        PetkitVisitsTodaySensor,
    )

    monkeypatch.setattr("custom_components.petkit_ble.coordinator.VISIT_POLL_INTERVAL", 0.05)
    c = await make_coordinator()
    pet = PetkitPetDetectedSensor(c)
    today = PetkitVisitsTodaySensor(c)
    month = PetkitVisitsMonthSensor(c)
    time_today = PetkitDrinkTimeTodaySensor(c)
    last = PetkitVisitSensor(c, 0, "Last Visit")
    last_len = PetkitVisitDurationSensor(c, 0, "Last Visit")
    assert pet.unique_id == "A4C138A57682_pet_detected"
    assert today.unique_id == "A4C138A57682_visits_today"
    assert last.unique_id == "A4C138A57682_visit_1"
    assert last_len.unique_id == "A4C138A57682_visit_1_duration"

    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        assert await wait_until(lambda: pet.is_on is False)
        assert today.native_value == 0
        assert last.native_value is None and last_len.native_value is None

        fountain.set_state_byte(19, 1)  # the cat arrives
        assert await wait_until(lambda: pet.is_on is True)
        assert await wait_until(lambda: c.visits.in_progress)
        assert today.native_value == 1 and month.native_value == 1

        fountain.set_state_byte(19, 2)  # firmware 111 reports 2: still just "present"
        await wait_until(lambda: False, timeout=0.3)
        assert today.native_value == 1  # not a second visit

        fountain.set_state_byte(19, 0)  # the cat leaves
        assert await wait_until(lambda: pet.is_on is False and not c.visits.in_progress)
        assert today.native_value == 1
        assert last.native_value is not None
        assert last_len.native_value == c.visits.visit(0)["seconds"]
        assert time_today.native_value == last_len.native_value
        assert last.extra_state_attributes["estimated"] is True
        assert last.extra_state_attributes["duration_seconds"] == last_len.native_value

        fountain.set_state_byte(19, 1)  # a second visit
        assert await wait_until(lambda: today.native_value == 2)
        fountain.set_state_byte(19, 0)
        assert await wait_until(lambda: not c.visits.in_progress)
        assert PetkitVisitSensor(c, 1, "Previous Visit").native_value is not None
    finally:
        await c.async_shutdown()


async def test_losing_the_link_ends_a_visit_in_progress(make_coordinator, fountain, monkeypatch):
    monkeypatch.setattr("custom_components.petkit_ble.coordinator.VISIT_POLL_INTERVAL", 0.05)
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        fountain.set_state_byte(19, 1)
        assert await wait_until(lambda: c.visits.in_progress)
        fountain.current.remote_drop()
        assert await wait_until(lambda: not c.visits.in_progress)
        assert c.visits.visit(0)["interrupted"] is True
        assert c.visits.today(datetime.now(timezone.utc).astimezone())["visits"] == 1
    finally:
        await c.async_shutdown()


async def test_power_source_low_battery_and_supply_voltage(make_coordinator, fountain):
    from custom_components.petkit_ble.binary_sensor import PetkitACPowerSensor, PetkitLowBatterySensor
    from custom_components.petkit_ble.sensor import PetkitSupplyVoltageSensor

    c = await make_coordinator()
    ac = PetkitACPowerSensor(c)
    low = PetkitLowBatterySensor(c)
    supply = PetkitSupplyVoltageSensor(c)
    assert (ac.unique_id, low.unique_id, supply.unique_id) == (
        "A4C138A57682_ac_power",
        "A4C138A57682_low_battery",
        "A4C138A57682_supply_voltage",
    )
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        assert await wait_until(lambda: supply.native_value == 5.481)  # bytes 20-21 = 0x1569 mV
        assert low.is_on is False
        assert ac.is_on is False  # byte 3 is 1 in the fixture
        assert ac.extra_state_attributes == {"electric_status_raw": 1}

        fountain.set_state_byte(3, 2)
        fountain.set_state_byte(7, 1)
        await c.async_request_refresh()
        assert await wait_until(lambda: ac.is_on is True and low.is_on is True)
    finally:
        await c.async_shutdown()


async def test_ctw3_only_entities_are_unknown_on_other_models(make_coordinator, fountain):
    from custom_components.petkit_ble.binary_sensor import PetkitACPowerSensor, PetkitPetDetectedSensor
    from custom_components.petkit_ble.sensor import PetkitSupplyVoltageSensor

    c = await make_coordinator()
    c.device.alias = "W5"
    assert PetkitPetDetectedSensor(c).is_on is None
    assert PetkitACPowerSensor(c).is_on is None
    assert PetkitSupplyVoltageSensor(c).native_value is None


def test_visit_history_sensor_keeps_big_attributes_out_of_the_database():
    from custom_components.petkit_ble.sensor import PetkitVisitHistorySensor

    assert {"daily", "monthly"} <= PetkitVisitHistorySensor._unrecorded_attributes
