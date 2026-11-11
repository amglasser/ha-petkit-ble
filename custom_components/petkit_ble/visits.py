"""Visit tracking for the CTW3 "pet detected" flag.

The fountain only reports whether a pet is at the bowl RIGHT NOW (command 210,
byte 19). This module turns that flag into visits:

* a visit starts at the first frame that shows the flag on and ends at the
  first frame that shows it off;
* the number of visits and the total time are kept per day (rolling 365 days)
  and per month (kept indefinitely);
* the last three visits are kept with their start, end and duration.

Because the flag is sampled (a poll every few seconds), a start is seen late and
an end is seen late by about the same amount, so durations are unbiased on
average, but a visit shorter than the sampling gap can be missed entirely and
nothing is counted while the fountain is out of reach. Everything derived from
this is therefore an estimate.

The class knows nothing about Home Assistant except the ``Store`` interface
(``async_load`` / ``async_delay_save`` / ``async_save``), so it can be tested
with a fake store.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
DAILY_RETENTION_DAYS = 365  # rolling window for the per-day figures
LAST_VISITS = 3  # how many recent visits are kept
SAVE_DELAY = 10  # seconds; writes are batched


def day_key(when: datetime | date) -> str:
    return when.date().isoformat() if isinstance(when, datetime) else when.isoformat()


def month_key(when: datetime | date) -> str:
    return f"{when.year:04d}-{when.month:02d}"


def format_duration(seconds: float | int | None) -> str | None:
    """Readable duration such as ``42 s`` or ``3 min 5 s``."""
    if seconds is None:
        return None
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


class VisitTracker:
    """Counts visits and drinking time from the pet-detected flag."""

    def __init__(self, store: Any) -> None:
        self._store = store
        # day key -> {"visits": int, "seconds": int}
        self.daily: dict[str, dict[str, int]] = {}
        # month key -> {"visits": int, "seconds": int}; never pruned
        self.monthly: dict[str, dict[str, int]] = {}
        # newest first: {"start": iso, "end": iso, "seconds": int, "interrupted": bool}
        self.last: list[dict[str, Any]] = []
        # The visit in progress (not persisted): when it started and when the flag was last seen on.
        self._open_start: datetime | None = None
        self._open_last_on: datetime | None = None
        self.last_flag: bool | None = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def async_load(self) -> None:
        """Load the saved history. A broken file never blocks the integration."""
        try:
            stored = await self._store.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not read the saved visit history, starting empty", exc_info=True)
            return
        if not isinstance(stored, dict):
            return
        self.daily = self._clean_totals(stored.get("daily"))
        self.monthly = self._clean_totals(stored.get("monthly"))
        last = stored.get("last")
        if isinstance(last, list):
            self.last = [
                v for v in last
                if isinstance(v, dict) and isinstance(v.get("start"), str)
            ][:LAST_VISITS]

    @staticmethod
    def _clean_totals(raw: Any) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            try:
                visits = max(int(value.get("visits", 0)), 0)
                seconds = max(int(value.get("seconds", 0)), 0)
            except (AttributeError, TypeError, ValueError):
                continue
            out[str(key)] = {"visits": visits, "seconds": seconds}
        return out

    def _snapshot(self) -> dict[str, Any]:
        return {"daily": self.daily, "monthly": self.monthly, "last": self.last}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._snapshot, SAVE_DELAY)

    async def async_save_now(self) -> None:
        try:
            await self._store.async_save(self._snapshot())
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not save the visit history", exc_info=True)

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------
    @property
    def in_progress(self) -> bool:
        return self._open_start is not None

    def update(self, detected: bool, now: datetime) -> bool:
        """Feed one observation of the flag. Returns True when the stats changed."""
        changed = False
        if detected:
            self._open_last_on = now
            if self._open_start is None:
                self._open_start = now
                self._add(now, visits=1, seconds=0)
                changed = True
        elif self._open_start is not None:
            self._finish(end=now, interrupted=False)
            changed = True
        self.last_flag = detected
        if changed:
            self._prune(now)
            self._schedule_save()
        return changed

    def close_open(self, interrupted: bool = True) -> bool:
        """The link dropped (or HA stops) while a visit was open: end it at the last time it was seen."""
        if self._open_start is None:
            return False
        end = self._open_last_on or self._open_start
        self._finish(end=end, interrupted=interrupted)
        self.last_flag = None
        self._schedule_save()
        return True

    def _finish(self, end: datetime, interrupted: bool) -> None:
        start = self._open_start
        assert start is not None
        seconds = max(int(round((end - start).total_seconds())), 0)
        self._add(start, visits=0, seconds=seconds)
        self.last.insert(
            0,
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "seconds": seconds,
                "interrupted": interrupted,
            },
        )
        del self.last[LAST_VISITS:]
        self._open_start = None
        self._open_last_on = None

    def _add(self, when: datetime, visits: int, seconds: int) -> None:
        """Book a visit (counted on the day and month it STARTED)."""
        for table, key in ((self.daily, day_key(when)), (self.monthly, month_key(when))):
            entry = table.setdefault(key, {"visits": 0, "seconds": 0})
            entry["visits"] += visits
            entry["seconds"] += seconds

    def _prune(self, now: datetime) -> None:
        """Keep a rolling year of daily figures. Monthly figures are kept forever."""
        cutoff = day_key(now.date() - timedelta(days=DAILY_RETENTION_DAYS))
        for key in [k for k in self.daily if k < cutoff]:
            del self.daily[key]

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------
    def today(self, now: datetime) -> dict[str, int]:
        return self.daily.get(day_key(now), {"visits": 0, "seconds": 0})

    def this_month(self, now: datetime) -> dict[str, int]:
        return self.monthly.get(month_key(now), {"visits": 0, "seconds": 0})

    def visit(self, index: int) -> dict[str, Any] | None:
        """The index-th most recent finished visit (0 = latest), or None."""
        return self.last[index] if index < len(self.last) else None

    @property
    def total_visits(self) -> int:
        return sum(v["visits"] for v in self.monthly.values())
