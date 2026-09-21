"""The last signal response, kept so a restart does not cost a call.

Setup reads every signal once, which is how capability is learned and how
entities get their first values. It is also one billed request, every time:
each restart, each reload, each reconfigure. Home Assistant restarts often, and
an evening of updates and configuration changes spent fifty calls out of a
monthly five hundred without the vehicle ever being asked anything new.

Nothing has changed in the car between a restart at one minute past and the
same restart a minute later, so the answer from a moment ago is reused and no
request is made. Beyond that window the data is treated as gone and setup reads
as it did before, which keeps a genuine restart after a long gap accurate.

The cache holds the whole signal response rather than a summary. Capability is
derived from the same body, so keeping it whole means a restored entry knows
what the vehicle cannot answer as well as what it last said.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "smartcar_signal_cache"

# how long a stored response may stand in for a fresh one at setup. long
# enough that a restart storm costs nothing, short enough that a restart after
# a real gap still reads the vehicle.
DEFAULT_MAX_AGE = dt.timedelta(minutes=30)


class SignalCache:
    """The most recent signal response per vehicle, persisted."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the cache."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}"
        )
        self._entries: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> SignalCache:
        """Read the stored responses.

        Returns:
            This cache, so it can be constructed and loaded in one expression.
        """
        self._entries = dict(await self._store.async_load() or {})

        return self

    async def async_store(self, vehicle_id: str, signal_data: dict[str, Any]) -> None:
        """Keep one vehicle's signal response."""
        self._entries[vehicle_id] = {
            "fetched_at": dt_util.utcnow().isoformat(),
            "data": signal_data,
        }
        await self._store.async_save(self._entries)

    def fresh(
        self, vehicle_id: str, max_age: dt.timedelta = DEFAULT_MAX_AGE
    ) -> dict[str, Any] | None:
        """The stored response, if it is recent enough to stand in for a call.

        Returns:
            The signal response, or None when there is none or it is too old.
        """
        entry = self._entries.get(vehicle_id)

        if not entry:
            return None

        # parse_datetime answers None for anything it cannot read, including
        # a missing key stringified, so there is nothing here to catch.
        fetched_at = dt_util.parse_datetime(str(entry.get("fetched_at")))

        if fetched_at is None:
            return None

        age = dt_util.utcnow() - fetched_at

        if age > max_age or age < dt.timedelta():
            _LOGGER.debug(
                "Cached signals for %s are %s old, which is not usable",
                vehicle_id,
                age,
            )
            return None

        data = entry.get("data")

        return data if isinstance(data, dict) else None
