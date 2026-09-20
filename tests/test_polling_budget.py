"""Tests for the API budget, the polling policy and webhook subscription.

Smartcar allows 500 API calls per vehicle per month on the free tier, and a
vehicle that is not subscribed to a webhook only refreshes its data about once
a day. So the integration's job is not to poll fast, it is to spend a small
allowance on the moments that are worth it. These tests pin the rules that
decide when that is.
"""

from datetime import timedelta
from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError, ClientResponseError, ClientSession, RequestInfo
from homeassistant.const import (
    CONF_WEBHOOK_ID,
    STATE_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.smartcar import async_subscribe_vehicles
from custom_components.smartcar.budget import (
    DEFAULT_MONTHLY_BUDGET,
    DEFAULT_RESERVE,
    ApiBudget,
)
from custom_components.smartcar.const import (
    CONF_APPLICATION_ID,
    CONF_AUTO_SUBSCRIBE,
    CONF_BUDGET_RESERVE,
    CONF_MONTHLY_BUDGET,
    CONF_POLL_INTERVAL_HOURS,
    CONF_POLL_PROFILE,
    CONF_PRESENCE_ENTITIES,
    DOMAIN,
)
from custom_components.smartcar.management import ManagementApi, webhook_id_matching_url
from custom_components.smartcar.polling import (
    MIN_INTERVAL,
    PollProfile,
    PollSettings,
    anyone_away,
    charge_wants_attention,
    is_away,
    presence_transition_wants_poll,
    resolve_settings,
)
from custom_components.smartcar.webhooks import webhook_url_from_id

from . import setup_integration

# ---------------------------------------------------------------- budget ----


async def test_budget_counts_per_vehicle(hass: HomeAssistant) -> None:
    """Calls are counted against the vehicle they were made to.

    Smartcar's allowance is per vehicle, not per account, so one busy car must
    not exhaust a quiet one.
    """
    budget = await ApiBudget(hass, "entry").async_load()

    await budget.async_record("car_a")
    await budget.async_record("car_a")
    await budget.async_record("car_b")

    assert budget.used("car_a") == 2
    assert budget.used("car_b") == 1
    assert budget.used("car_c") == 0


async def test_budget_survives_a_reload(hass: HomeAssistant) -> None:
    """Counts persist, or a restart would silently reset the allowance."""
    budget = await ApiBudget(hass, "entry").async_load()
    await budget.async_record("car", calls=7)

    reloaded = await ApiBudget(hass, "entry").async_load()

    assert reloaded.used("car") == 7


async def test_budget_starts_over_in_a_new_period(hass: HomeAssistant) -> None:
    """A stored count from last month is discarded, not carried forward.

    Smartcar has already reset its own side, so keeping the old number would
    make the integration refuse to poll for a month.
    """
    budget = await ApiBudget(hass, "entry").async_load()
    await budget.async_record("car", calls=400)

    with patch("custom_components.smartcar.budget._period_of", return_value="2099-01"):
        fresh = await ApiBudget(hass, "entry").async_load()
        assert fresh.used("car") == 0


async def test_budget_reserves_calls_for_commands(hass: HomeAssistant) -> None:
    """Polling stops short of the ceiling so a command still has room.

    Commands are billed from the same allowance. A budget spent entirely on
    readings is a lock that cannot be sent.
    """
    budget = await ApiBudget(hass, "entry").async_load()
    await budget.async_record("car", calls=460)

    assert budget.remaining("car", 500) == 40
    assert budget.can_poll("car", 500, reserve=50) is False
    assert budget.can_poll("car", 500, reserve=20) is True


async def test_budget_remaining_never_goes_negative(hass: HomeAssistant) -> None:
    """Overspending reports zero left rather than a negative allowance."""
    budget = await ApiBudget(hass, "entry").async_load()
    await budget.async_record("car", calls=600)

    assert budget.remaining("car", 500) == 0
    assert budget.period == budget.period


# --------------------------------------------------------------- polling ----


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (PollProfile.WEBHOOK_ONLY, None),
        (PollProfile.ON_DEMAND, None),
        (PollProfile.DAILY, timedelta(hours=24)),
        (PollProfile.TWICE_DAILY, timedelta(hours=12)),
    ],
)
def test_profile_intervals(profile: PollProfile, expected: timedelta | None) -> None:
    """Each profile schedules what it says it does."""
    assert PollSettings(profile=profile).scheduled_interval == expected


def test_custom_interval_has_a_floor() -> None:
    """An interval below an hour is a guaranteed billing error, not a choice.

    Hourly is already 720 calls a month against an allowance of 500.
    """
    settings = PollSettings(profile=PollProfile.INTERVAL, interval=timedelta(minutes=5))

    assert settings.scheduled_interval == MIN_INTERVAL


def test_monthly_estimate_counts_events_at_their_cap() -> None:
    """The estimate is a ceiling, so it can be trusted next to the allowance."""
    settings = PollSettings(
        profile=PollProfile.TWICE_DAILY,
        presence_entities=("person.sean",),
        daily_event_cap=2,
    )

    assert settings.monthly_estimate() == 60 + 60


def test_estimate_of_a_profile_that_never_schedules() -> None:
    """Webhook only schedules nothing, so it estimates nothing."""
    assert PollSettings(profile=PollProfile.WEBHOOK_ONLY).monthly_estimate() == 0


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("not_home", True),
        ("away", True),
        ("Work", True),
        (STATE_HOME, False),
        (STATE_UNKNOWN, False),
        (STATE_UNAVAILABLE, False),
        (None, False),
    ],
)
def test_unknown_presence_is_not_away(state: str | None, expected: bool) -> None:
    """A tracker dropping out is not a departure.

    A phone rebooting must not read as the car leaving and spend a call.
    """
    assert is_away(state) is expected


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (STATE_HOME, "not_home", True),
        ("not_home", STATE_HOME, False),
        (STATE_HOME, STATE_HOME, False),
        ("not_home", "Work", False),
        (STATE_UNKNOWN, "not_home", False),
        (None, "not_home", False),
        (STATE_UNAVAILABLE, "not_home", False),
    ],
    ids=[
        "left_home",
        "arrived_home_not_wanted",
        "no_change",
        "away_to_away",
        "waking_from_unknown",
        "first_ever_state",
        "waking_from_unavailable",
    ],
)
def test_only_real_boundary_crossings_poll(
    old: str | None, new: str | None, expected: bool
) -> None:
    """Only a genuine home boundary crossing is worth an API call."""
    settings = PollSettings(presence_entities=("person.sean",))

    assert presence_transition_wants_poll(settings, old, new) is expected


def test_arriving_home_can_be_opted_into() -> None:
    """Arrival is off by default but available for people who want it."""
    settings = PollSettings(
        presence_entities=("person.sean",),
        poll_on_leave_home=False,
        poll_on_arrive_home=True,
    )

    assert presence_transition_wants_poll(settings, "not_home", STATE_HOME) is True
    assert presence_transition_wants_poll(settings, STATE_HOME, "not_home") is False


def test_watches_presence_needs_both_an_entity_and_an_edge() -> None:
    """Configuring entities but no edges watches nothing."""
    assert PollSettings().watches_presence is False
    assert (
        PollSettings(
            presence_entities=("person.sean",),
            poll_on_leave_home=False,
            poll_on_arrive_home=False,
        ).watches_presence
        is False
    )
    assert PollSettings(presence_entities=("person.sean",)).watches_presence is True


def test_anyone_away_ignores_missing_entities(hass: HomeAssistant) -> None:
    """A deleted tracker is not a person standing in the driveway."""
    hass.states.async_set("person.sean", "not_home")

    assert anyone_away(hass, ["person.sean"]) is True
    assert anyone_away(hass, ["person.gone"]) is False


@pytest.mark.parametrize(
    ("level", "charging", "expected"),
    [
        (20, True, True),
        (80, True, False),
        (20, False, False),
        (None, True, False),
    ],
    ids=["charging_low", "charging_high", "idle_low", "unknown_level"],
)
def test_charge_state_worth_a_call(
    level: float | None, charging: bool, expected: bool
) -> None:
    """Only a vehicle charging below the threshold changes usefully by the hour.

    A full idle car is the case where more polling buys nothing at all.
    """
    settings = PollSettings(low_battery=30)

    assert charge_wants_attention(settings, level, is_charging=charging) is expected


def test_unknown_profile_falls_back_rather_than_failing() -> None:
    """A profile this version does not know must not break setup."""
    settings = resolve_settings({CONF_POLL_PROFILE: "from_the_future"})

    assert settings.profile is PollProfile.TWICE_DAILY


def test_settings_resolve_from_stored_options() -> None:
    """The options screen's answers reach the policy."""
    settings = resolve_settings(
        {
            CONF_POLL_PROFILE: PollProfile.INTERVAL.value,
            CONF_POLL_INTERVAL_HOURS: 8,
            CONF_PRESENCE_ENTITIES: ["person.sean", "device_tracker.van"],
        }
    )

    assert settings.profile is PollProfile.INTERVAL
    assert settings.interval == timedelta(hours=8)
    assert settings.presence_entities == ("person.sean", "device_tracker.van")


# ------------------------------------------------------------ management ----


def test_webhook_matched_by_callback_url() -> None:
    """The right webhook is the one pointing at this Home Assistant.

    An application can have several webhooks for other consumers. Subscribing
    to the wrong one sends this instance nothing.
    """
    webhooks = [
        {"id": "wh_other", "attributes": {"callbackUri": "https://example.com/hook"}},
        {"id": "wh_mine", "attributes": {"callbackUri": "https://ha.example/api/x/"}},
        {"id": "wh_broken", "attributes": "not a dict"},
    ]

    assert webhook_id_matching_url(webhooks, "https://ha.example/api/x") == "wh_mine"
    assert webhook_id_matching_url(webhooks, "https://nowhere.example") is None
    assert webhook_id_matching_url([{"attributes": {"url": "u"}}], "u") is None


class _FakeResponse:
    """A response whose raise_for_status is synchronous, as aiohttp's is.

    AsyncMock makes raise_for_status a coroutine, so a side effect set on it
    never fires for code that calls it without awaiting, which is what real
    aiohttp requires. That made an earlier version of these tests pass while
    asserting nothing.
    """

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self._body = body if body is not None else {}

    def raise_for_status(self) -> None:
        """Raise for a 4xx or 5xx status.

        Raises:
            ClientResponseError: When the status is an error.
        """
        if self.status >= HTTPStatus.BAD_REQUEST:
            raise _response_error(HTTPStatus(self.status))

    async def json(self, **_kwargs: Any) -> Any:
        """Return the canned body.

        Returns:
            The body.
        """
        return self._body


class _FakeSession:
    """Just enough aiohttp session to drive ManagementApi."""

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **_kwargs: Any) -> _FakeResponse:
        """Record and answer the request.

        Returns:
            The canned response.

        Raises:
            Exception: When the fake was built with one.
        """
        self.calls.append((method, url))

        if isinstance(self._response, Exception):
            raise self._response

        return self._response


def _api(response: _FakeResponse | Exception) -> ManagementApi:
    """Build a ManagementApi over a fake session.

    Returns:
        The client under test.
    """
    auth = AsyncMock()
    auth.async_get_access_token.return_value = "token"

    return ManagementApi(auth, cast("ClientSession", _FakeSession(response)))


async def test_subscribe_treats_conflict_as_success() -> None:
    """An existing subscription is the desired end state, not an error."""
    api = _api(_FakeResponse(HTTPStatus.CONFLICT))

    assert await api.async_subscribe(webhook_id="w", user_id="u", vehicle_id="v")


async def test_subscribe_reports_other_failures() -> None:
    """A real failure returns False rather than pretending to have worked."""
    api = _api(_FakeResponse(HTTPStatus.FORBIDDEN))

    assert not await api.async_subscribe(webhook_id="w", user_id="u", vehicle_id="v")


async def test_subscribe_succeeds_on_accepted() -> None:
    """The endpoint answers 202 because it creates the subscription async."""
    api = _api(_FakeResponse(HTTPStatus.ACCEPTED, {"meta": {"message": "queued"}}))

    assert await api.async_subscribe(webhook_id="w", user_id="u", vehicle_id="v")


async def test_listing_failures_are_not_fatal() -> None:
    """Setup continues when the Management API cannot be read.

    Subscription is a convenience. Failing setup over it would take the whole
    integration away because of an optional extra.
    """
    api = _api(ClientError("nope"))

    assert await api.async_list_webhooks() == []
    assert await api.async_subscriptions_for_vehicle("v") == []
    assert await api.async_unsubscribe("s") is False


async def test_listing_tolerates_an_unexpected_body() -> None:
    """A body that is not the documented shape yields nothing, not a crash."""
    api = _api(_FakeResponse(HTTPStatus.OK, []))

    assert await api.async_list_webhooks() == []
    assert await api.async_subscriptions_for_vehicle("v") == []


async def test_listing_returns_the_resources() -> None:
    """The happy path reads data[] and drops anything malformed."""
    api = _api(_FakeResponse(HTTPStatus.OK, {"data": [{"id": "a"}, "junk"]}))

    assert await api.async_list_webhooks() == [{"id": "a"}]
    assert await api.async_subscriptions_for_vehicle("v") == [{"id": "a"}]


async def test_unsubscribe_of_a_missing_subscription_is_success() -> None:
    """Gone is the desired end state."""
    api = _api(_FakeResponse(HTTPStatus.NOT_FOUND))

    assert await api.async_unsubscribe("sub_1") is True


async def test_unsubscribe_reports_other_failures() -> None:
    """A subscription that could not be removed says so."""
    api = _api(_FakeResponse(HTTPStatus.FORBIDDEN))

    assert await api.async_unsubscribe("sub_1") is False


async def test_unsubscribe_handles_no_content() -> None:
    """A 204 carries no body and must not be parsed as one."""
    api = _api(_FakeResponse(HTTPStatus.NO_CONTENT))

    assert await api.async_unsubscribe("sub_1") is True


def _response_error(status: HTTPStatus) -> ClientResponseError:
    """Build a ClientResponseError with a status.

    Returns:
        The error.
    """
    url = URL("https://management.api.smartcar.com/v3/subscriptions")

    return ClientResponseError(
        request_info=RequestInfo(
            url=url,
            method="POST",
            headers=CIMultiDictProxy(CIMultiDict()),
            real_url=url,
        ),
        history=(),
        status=status,
    )


# ------------------------------------------------------------ integration ---


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_scheduled_poll_stops_at_the_reserve(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A vehicle out of allowance is not polled, and says why.

    Silently going quiet would look like a broken integration. The log names
    the allowance, the reserve, and the way out.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.budget.async_record(coordinator.vehicle_id, calls=500)

    caplog.clear()
    await coordinator.async_refresh()

    assert "skipping scheduled poll" in caplog.text
    assert "webhook" in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_event_polls_are_capped_per_day(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A tracker flapping between home and away cannot drain the month."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    coordinator.settings = PollSettings(daily_event_cap=2)

    assert await coordinator.async_request_event_poll("first") is True
    assert await coordinator.async_request_event_poll("second") is True
    assert await coordinator.async_request_event_poll("third") is False


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_event_poll_respects_the_budget(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """An event cannot spend the reserve either."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.budget.async_record(coordinator.vehicle_id, calls=500)

    assert await coordinator.async_request_event_poll("someone left") is False


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_leaving_home_refreshes_the_vehicle(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """The cheapest trigger there is: a phone that already moved.

    Watching presence costs nothing and says more about whether the car moved
    than a timer does.
    """
    hass.states.async_set("person.sean", STATE_HOME)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_POLL_PROFILE: PollProfile.ON_DEMAND.value,
            CONF_PRESENCE_ENTITIES: ["person.sean"],
        },
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    with patch.object(
        coordinator, "async_request_refresh", AsyncMock()
    ) as mock_refresh:
        hass.states.async_set("person.sean", "not_home")
        await hass.async_block_till_done()

        assert mock_refresh.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_presence_noise_does_not_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A tracker waking from unknown is not a departure."""
    hass.states.async_set("person.sean", STATE_UNKNOWN)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_POLL_PROFILE: PollProfile.ON_DEMAND.value,
            CONF_PRESENCE_ENTITIES: ["person.sean"],
        },
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    with patch.object(
        coordinator, "async_request_refresh", AsyncMock()
    ) as mock_refresh:
        hass.states.async_set("person.sean", "not_home")
        await hass.async_block_till_done()

        assert not mock_refresh.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_refresh_action_refuses_when_the_allowance_is_gone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """The action tells the user why rather than failing quietly."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.budget.async_record(coordinator.vehicle_id, calls=500)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            "refresh_vehicle",
            {"config_entry": mock_config_entry.entry_id},
            blocking=True,
        )

    assert excinfo.value.translation_key == "budget_exhausted"


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_refresh_action_can_be_forced(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Deliberately spending the reserve stays possible."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.budget.async_record(coordinator.vehicle_id, calls=500)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            "refresh_vehicle",
            {"config_entry": mock_config_entry.entry_id, "force": True},
            blocking=True,
        )

        assert refresh.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_refresh_action_validates_its_target(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """An unknown entry or VIN is a user error, reported as one."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh_vehicle",
            {"config_entry": "nope"},
            blocking=True,
        )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh_vehicle",
            {"config_entry": mock_config_entry.entry_id, "vin": "NOT_A_VIN"},
            blocking=True,
        )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_budget_sensors_report_the_allowance(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """The user can see what has been spent without reading the Smartcar site."""
    await setup_integration(hass, mock_config_entry)

    used = hass.states.get("sensor.vw_id_4_api_calls_used")
    remaining = hass.states.get("sensor.vw_id_4_api_calls_remaining")

    assert used is not None
    assert remaining is not None
    assert int(used.state) + int(remaining.state) == DEFAULT_MONTHLY_BUDGET
    assert used.attributes["reserved_for_commands"] == DEFAULT_RESERVE
    assert used.attributes["monthly_budget"] == DEFAULT_MONTHLY_BUDGET


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_budget_and_reserve_are_configurable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A paid plan is not 500 calls, so the allowance is not hard coded."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_MONTHLY_BUDGET: 5000, CONF_BUDGET_RESERVE: 100},
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    assert coordinator.monthly_budget == 5000
    assert coordinator.budget_reserve == 100


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_vehicles_are_subscribed_to_the_matching_webhook(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Setup subscribes each vehicle to the webhook pointing at this instance.

    This is what removes the manual per-vehicle step in the Smartcar dashboard,
    and a subscribed vehicle is the only way to get data that is both fresh and
    free of the monthly allowance.
    """
    await setup_integration(hass, mock_config_entry)

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "user_id": "user_1",
            CONF_WEBHOOK_ID: mock_config_entry.data.get(CONF_WEBHOOK_ID, "wh_local"),
        },
    )

    runtime = mock_config_entry.runtime_data
    callback_url, _ = await webhook_url_from_id(
        hass, mock_config_entry.data[CONF_WEBHOOK_ID]
    )

    with (
        patch.object(
            runtime.management,
            "async_list_webhooks",
            AsyncMock(
                return_value=[
                    {"id": "wh_1", "attributes": {"callbackUri": callback_url}}
                ]
            ),
        ),
        patch.object(
            runtime.management,
            "async_subscriptions_for_vehicle",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            runtime.management, "async_subscribe", AsyncMock(return_value=True)
        ) as subscribe,
    ):
        await async_subscribe_vehicles(hass, mock_config_entry)

    assert subscribe.await_args.kwargs["webhook_id"] == "wh_1"
    assert subscribe.await_args.kwargs["vehicle_id"] == vehicle["id"]


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_already_subscribed_vehicles_are_left_alone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Subscribing twice would be a pointless write on every restart."""
    await setup_integration(hass, mock_config_entry)

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "user_id": "user_1",
            CONF_WEBHOOK_ID: mock_config_entry.data.get(CONF_WEBHOOK_ID, "wh_local"),
        },
    )

    runtime = mock_config_entry.runtime_data
    callback_url, _ = await webhook_url_from_id(
        hass, mock_config_entry.data[CONF_WEBHOOK_ID]
    )

    with (
        patch.object(
            runtime.management,
            "async_list_webhooks",
            AsyncMock(
                return_value=[
                    {"id": "wh_1", "attributes": {"callbackUri": callback_url}}
                ]
            ),
        ),
        patch.object(
            runtime.management,
            "async_subscriptions_for_vehicle",
            AsyncMock(return_value=[{"id": "sub_1"}]),
        ),
        patch.object(runtime.management, "async_subscribe", AsyncMock()) as subscribe,
    ):
        await async_subscribe_vehicles(hass, mock_config_entry)

    assert not subscribe.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_no_matching_webhook_is_explained(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A webhook for someone else is not this instance's webhook.

    Subscribing a vehicle to it would send this instance nothing and the other
    consumer data it did not ask for, so the URL has to match.
    """
    await setup_integration(hass, mock_config_entry)

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "user_id": "user_1",
            CONF_WEBHOOK_ID: mock_config_entry.data.get(CONF_WEBHOOK_ID, "wh_local"),
        },
    )

    runtime = mock_config_entry.runtime_data

    with (
        patch.object(
            runtime.management,
            "async_list_webhooks",
            AsyncMock(
                return_value=[
                    {"id": "wh_other", "attributes": {"callbackUri": "https://x/y"}}
                ]
            ),
        ),
        patch.object(runtime.management, "async_subscribe", AsyncMock()) as subscribe,
    ):
        caplog.clear()
        await async_subscribe_vehicles(hass, mock_config_entry)

    assert not subscribe.called
    assert "No Smartcar webhook is configured with the callback URL" in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_subscription_needs_a_user_id(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Without the user id there is nothing to scope a subscription to."""
    await setup_integration(hass, mock_config_entry)

    runtime = mock_config_entry.runtime_data
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={k: v for k, v in mock_config_entry.data.items() if k != "user_id"},
    )

    with patch.object(runtime.management, "async_list_webhooks", AsyncMock()) as listed:
        await async_subscribe_vehicles(hass, mock_config_entry)

    assert not listed.called


async def test_budget_rolls_over_mid_session(hass: HomeAssistant) -> None:
    """The month can turn over while Home Assistant is running.

    Holding last month's count would make the integration refuse to poll until
    a restart.
    """
    budget = await ApiBudget(hass, "entry").async_load()
    await budget.async_record("car", calls=500)

    assert budget.used("car") == 500

    with patch("custom_components.smartcar.budget._period_of", return_value="2099-02"):
        assert budget.used("car") == 0
        assert budget.period == "2099-02"


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_coordinator_without_a_budget_always_polls(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A coordinator built without accounting is not silently blocked."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    coordinator.budget = None

    assert coordinator.budget_allows_poll() is True
    await coordinator.async_record_call()


async def test_subscribe_handles_a_transport_failure() -> None:
    """A network failure during subscribe is reported, not raised."""
    api = _api(ClientError("down"))

    assert not await api.async_subscribe(webhook_id="w", user_id="u", vehicle_id="v")


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_no_webhook_means_nothing_to_subscribe_to(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """An entry without webhooks configured subscribes nothing."""
    await setup_integration(hass, mock_config_entry)

    runtime = mock_config_entry.runtime_data
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **{k: v for k, v in mock_config_entry.data.items() if k != CONF_WEBHOOK_ID},
            "user_id": "user_1",
        },
    )

    with patch.object(runtime.management, "async_list_webhooks", AsyncMock()) as listed:
        await async_subscribe_vehicles(hass, mock_config_entry)

    assert not listed.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_auto_subscribe_runs_at_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """The subscription attempt is made during setup when it is enabled.

    It runs as a background task so a slow Management API cannot hold up the
    entry, which is why this asserts the task was started rather than awaited.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_WEBHOOK_ID: "wh_local"},
        options={CONF_AUTO_SUBSCRIBE: True},
    )

    with patch(
        "custom_components.smartcar.async_subscribe_vehicles", AsyncMock()
    ) as subscribe:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert subscribe.called


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_options_flow_does_not_reload_when_nothing_changed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Saving the options screen unchanged must not restart the integration.

    The entry carries no update listener by design, so the flow schedules its
    own reload. Doing that unconditionally would make every visit to the
    options screen tear down and rebuild every entity for nothing.
    """
    await setup_integration(hass, mock_config_entry)

    async def _run_flow(webhooks: bool) -> None:
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_POLL_PROFILE: PollProfile.TWICE_DAILY.value},
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "use_webhooks": webhooks,
                CONF_APPLICATION_ID: mock_config_entry.data.get(
                    CONF_APPLICATION_ID, "app_1"
                ),
            },
        )
        await hass.async_block_till_done()

    # the first run writes the answers, so something genuinely changed
    await _run_flow(webhooks=False)

    # async_update_entry reports whether anything actually changed. when it
    # says nothing did, the flow must not reload: that is the difference
    # between opening the options screen and restarting the integration.
    with (
        patch.object(hass.config_entries, "async_update_entry", return_value=False),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        await _run_flow(webhooks=False)

    assert not reload.called
