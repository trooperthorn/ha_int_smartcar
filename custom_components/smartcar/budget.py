"""Accounting for Smartcar's per-vehicle monthly request allowance.

Smartcar bills API calls **per vehicle, per billing period**, not per account:
the free tier allows 500 calls to each single vehicle per month, and the 501st
answers `430 BILLING / VEHICLE_REQUEST_LIMIT` for the rest of the period. One
`GET /vehicles/{id}/signals` is one call however many signals come back, so the
unit to count is the request, not the datapoint.

Running out is worse than it sounds, because commands are billed the same way.
A budget spent on polling is a lock or a charge stop that cannot be sent, which
is why `reserve` exists: a number of calls that scheduled polling may not touch
so that there is always something left to act with.

Application-level calls are deliberately not counted. `/connections` is not
addressed to a vehicle and does not come out of a vehicle's allowance.

The count is kept in Home Assistant storage rather than in the config entry so
that it survives restarts without rewriting the entry on every poll. Smartcar
does not publish the billing period boundary, so this tracks calendar months in
the user's timezone and says so: the number is an estimate of what Smartcar has
counted, useful for staying clear of the ceiling rather than for reconciling a
bill.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Self

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "smartcar_api_budget"

DEFAULT_MONTHLY_BUDGET = 500
DEFAULT_RESERVE = 50


def _period_of(moment: dt.datetime) -> str:
    """Name the billing period a moment falls in.

    Returns:
        The period key, `YYYY-MM` in local time.
    """
    return dt_util.as_local(moment).strftime("%Y-%m")


class ApiBudget:
    """Per-vehicle call counts for the current period, persisted."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the budget."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}"
        )
        self._period: str = _period_of(dt_util.utcnow())
        self._counts: dict[str, int] = {}
        self._loaded = False

    async def async_load(self) -> Self:
        """Read the stored counts, discarding any from an earlier period.

        Returns:
            This budget, so it can be constructed and loaded in one expression.
        """
        stored = await self._store.async_load() or {}
        period = _period_of(dt_util.utcnow())

        if stored.get("period") == period:
            self._counts = dict(stored.get("counts", {}))
        else:
            # a new month starts from zero. the old counts are not worth
            # keeping: Smartcar has already reset its own side.
            self._counts = {}

        self._period = period
        self._loaded = True

        return self

    async def async_record(self, vehicle_id: str, calls: int = 1) -> None:
        """Count calls made to one vehicle."""
        self._roll_period()
        self._counts[vehicle_id] = self._counts.get(vehicle_id, 0) + calls
        await self._async_save()

    def used(self, vehicle_id: str) -> int:
        """Calls counted against a vehicle this period.

        Returns:
            The count, zero if the vehicle has not been called.
        """
        self._roll_period()
        return self._counts.get(vehicle_id, 0)

    def remaining(self, vehicle_id: str, budget: int) -> int:
        """Calls left before the vehicle's allowance is gone.

        Returns:
            The remaining count, never below zero.
        """
        return max(budget - self.used(vehicle_id), 0)

    def can_poll(self, vehicle_id: str, budget: int, reserve: int) -> bool:
        """Whether a scheduled poll may spend a call.

        Commands are billed from the same allowance, so polling stops short of
        the ceiling and leaves `reserve` calls for them.

        Returns:
            True when spending one call would still leave the reserve intact.
        """
        return self.remaining(vehicle_id, budget) > reserve

    def _roll_period(self) -> None:
        """Drop the counts when the calendar month has turned over."""
        period = _period_of(dt_util.utcnow())

        if period != self._period:
            _LOGGER.debug(
                "Budget period rolled from %s to %s; counts reset",
                self._period,
                period,
            )
            self._period = period
            self._counts = {}

    async def _async_save(self) -> None:
        await self._store.async_save({"period": self._period, "counts": self._counts})

    @property
    def period(self) -> str:
        """The period the current counts belong to.

        Returns:
            The period key.
        """
        self._roll_period()
        return self._period
