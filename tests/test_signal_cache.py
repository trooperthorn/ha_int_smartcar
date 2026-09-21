"""Tests for not paying a call every time Home Assistant restarts.

Setup reads every signal once, which learns capability and gives entities their
first values, and that read is billed. Home Assistant restarts often: an
evening of updates and configuration changes spent fifty calls out of a monthly
five hundred without the vehicle being asked anything new.

See docs/polling.md for the allowance this protects.
"""

import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartcar.cache import DEFAULT_MAX_AGE, SignalCache
from custom_components.smartcar.const import DOMAIN
from custom_components.smartcar.coordinator import SmartcarVehicleCoordinator

VEHICLE = "8e5a8dca-a62d-466b-96f7-d204ff1787a8"
RESPONSE: dict[str, Any] = {
    "data": [{"attributes": {"code": "tractionbattery-stateofcharge", "value": 62}}],
    "meta": {"totalCount": 1},
}


async def test_a_stored_response_stands_in_for_a_call(hass: HomeAssistant) -> None:
    """A restart a moment later reuses what the vehicle already said."""
    cache = await SignalCache(hass, "entry").async_load()
    await cache.async_store(VEHICLE, RESPONSE)

    assert cache.fresh(VEHICLE) == RESPONSE


async def test_it_survives_a_restart(hass: HomeAssistant) -> None:
    """The point is restarts, so the store has to be read back from disk."""
    await (await SignalCache(hass, "entry").async_load()).async_store(VEHICLE, RESPONSE)

    restarted = await SignalCache(hass, "entry").async_load()

    assert restarted.fresh(VEHICLE) == RESPONSE


async def test_an_old_response_is_not_reused(hass: HomeAssistant) -> None:
    """Past the window the vehicle is asked again, so a real gap stays honest."""
    cache = await SignalCache(hass, "entry").async_load()
    await cache.async_store(VEHICLE, RESPONSE)

    assert cache.fresh(VEHICLE, dt.timedelta(seconds=0)) is None


async def test_an_unknown_vehicle_has_nothing_stored(hass: HomeAssistant) -> None:
    """Nothing cached means the call happens as it always did."""
    cache = await SignalCache(hass, "entry").async_load()

    assert cache.fresh(VEHICLE) is None


@pytest.mark.parametrize(
    "stored",
    [
        {"fetched_at": "not a date", "data": RESPONSE},
        {"data": RESPONSE},
        {"fetched_at": None, "data": RESPONSE},
    ],
    ids=["unreadable", "missing", "null"],
)
async def test_an_unreadable_timestamp_is_not_trusted(
    hass: HomeAssistant, stored: dict[str, Any]
) -> None:
    """Without a usable time there is no way to know the age, so it is not used."""
    cache = await SignalCache(hass, "entry").async_load()
    cache._entries[VEHICLE] = stored

    assert cache.fresh(VEHICLE) is None


async def test_a_future_timestamp_is_not_trusted(hass: HomeAssistant) -> None:
    """A clock that moved backwards must not make stale data look current."""
    cache = await SignalCache(hass, "entry").async_load()
    cache._entries[VEHICLE] = {
        "fetched_at": (dt_util.utcnow() + dt.timedelta(hours=1)).isoformat(),
        "data": RESPONSE,
    }

    assert cache.fresh(VEHICLE) is None


async def test_a_body_that_is_not_an_object_is_not_returned(
    hass: HomeAssistant,
) -> None:
    """Storage can hold anything; only a response shaped body is usable."""
    cache = await SignalCache(hass, "entry").async_load()
    cache._entries[VEHICLE] = {
        "fetched_at": dt_util.utcnow().isoformat(),
        "data": "not a response",
    }

    assert cache.fresh(VEHICLE) is None


def test_the_window_is_short_enough_to_stay_honest() -> None:
    """Long enough that a restart storm is free, short enough to stay current.

    A vehicle that has genuinely been asleep for hours must still be read at
    setup, so this window cannot grow into a polling interval by accident.
    """
    assert dt.timedelta(hours=1) >= DEFAULT_MAX_AGE


async def test_setup_spends_no_call_when_the_answer_is_still_current(
    hass: HomeAssistant,
) -> None:
    """The coordinator uses the stored response instead of asking again.

    This is the behaviour the cache exists for: a restart reuses what the
    vehicle said a moment ago. The auth here raises on any request, so a call
    would fail the test rather than quietly costing a call.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={"vehicles": {}}, options={})
    entry.add_to_hass(hass)

    cache = await SignalCache(hass, entry.entry_id).async_load()
    await cache.async_store(VEHICLE, RESPONSE)

    class _Auth:
        version = "v3"
        user_id = None

        @staticmethod
        async def request_v3(*args: Any, **kwargs: Any) -> None:  # noqa: ARG004
            """Fail the test.

            Raises:
                AssertionError: Always: no request should be made.
            """
            message = "setup made a billed request when the cache was current"
            raise AssertionError(message)

    coordinator = SmartcarVehicleCoordinator(
        hass,
        auth=_Auth(),  # type: ignore[arg-type]
        vehicle_id=VEHICLE,
        vin="",
        entry=entry,
        version="v3",
        cache=cache,
    )

    assert await coordinator.async_load_capabilities() is True
    assert coordinator.data


async def test_without_a_cache_the_call_happens_as_it_always_did(
    hass: HomeAssistant,
) -> None:
    """The cache is an optimisation, so its absence must change nothing."""
    entry = MockConfigEntry(domain=DOMAIN, data={"vehicles": {}}, options={})
    entry.add_to_hass(hass)

    class _Response:
        status = 200

        @staticmethod
        def raise_for_status() -> None:
            """Succeed."""

        @staticmethod
        async def json() -> dict[str, Any]:
            """Answer with a signal response.

            Returns:
                The response.
            """
            return RESPONSE

    class _Auth:
        version = "v3"
        user_id = None

        @staticmethod
        async def request_v3(*args: Any, **kwargs: Any) -> _Response:  # noqa: ARG004
            """Answer every request the same way.

            Returns:
                The response.
            """
            return _Response()

    coordinator = SmartcarVehicleCoordinator(
        hass,
        auth=_Auth(),  # type: ignore[arg-type]
        vehicle_id=VEHICLE,
        vin="",
        entry=entry,
        version="v3",
    )

    assert await coordinator.async_load_capabilities() is True
    assert coordinator.cache is None
