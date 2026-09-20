"""Tests for reading what a vehicle can actually do, and for command outcomes.

Support varies by make, model, year and region: a 2025 US ID. Buzz answers 23
of the 95 signals Smartcar publishes. Entities used to be created from the
granted scopes alone, so a vehicle with no diagnostics still got diagnostic
entities that could never hold a value.

See docs/api-reference.md for the API surface these tests pin.
"""

from http import HTTPStatus
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartcar import (
    CONNECTIONS_PAGE_LIMIT,
    _fetch_all_connections,  # noqa: PLC2701
    coordinator as coordinator_module,
)
from custom_components.smartcar.auth import AbstractAuth
from custom_components.smartcar.const import EntityDescriptionKey
from custom_components.smartcar.entity import async_send_command
from custom_components.smartcar.types import SmartcarAPIError

from . import MOCK_API_ENDPOINT, setup_integration


def _signal(code: str, error_type: str | None = None) -> dict[str, Any]:
    status: dict[str, Any] = (
        {"value": "SUCCESS"}
        if error_type is None
        else {"value": "ERROR", "error": {"type": error_type, "code": "ANY"}}
    )
    return {"attributes": {"code": code, "status": status}}


def test_only_compatibility_errors_mean_incapable() -> None:
    """Capability is a permanent property, so only COMPATIBILITY counts.

    VEHICLE_STATE means "not right now": charge rate reads as an error while
    the car is unplugged, and that entity must keep existing. PERMISSION means
    the user needs to re-consent, which is also not a statement about what the
    vehicle can do. Treating either as incapacity would delete working
    entities.
    """
    codes = coordinator_module._incapable_codes(
        {
            "data": [
                _signal("closure-sunroof", "COMPATIBILITY"),
                _signal("charge-chargerate", "VEHICLE_STATE"),
                _signal("closure-fronttrunk", "PERMISSION"),
                _signal("tractionbattery-stateofcharge"),
            ]
        }
    )

    assert codes == frozenset({"closure-sunroof"})


def test_no_signals_means_nothing_is_known_to_be_incapable() -> None:
    """An empty or absent list yields an empty set, never a full one.

    The set is a list of things to hide, so an empty one is the safe default:
    unknown capability creates every entity, as before this existed.
    """
    assert coordinator_module._incapable_codes({}) == frozenset()
    assert coordinator_module._incapable_codes({"data": []}) == frozenset()


def _registry_keys(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, entry: MockConfigEntry
) -> set[str]:
    """Collect the datapoint key of every entity created for an entry.

    Entities that are disabled by default still exist in the registry, so this
    is what the capability gate has to be judged on. A state check would pass
    for the wrong reason.

    Returns:
        The datapoint key part of each registered unique id.
    """
    return {
        registry_entry.unique_id.split("_", 1)[1]
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
    }


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_incapable_signals_get_no_entity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """A signal the vehicle cannot answer produces no entity at all.

    The fixture reports COMPATIBILITY / VEHICLE_NOT_CAPABLE for the sunroof,
    the connectivity signals and the combustion signals on this BEV, and a
    PERMISSION error for the front trunk. Only the first kind is hidden.
    """
    await setup_integration(hass, mock_config_entry)

    keys = _registry_keys(hass, entity_registry, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    assert "closure-sunroof" in coordinator.incapable_codes
    assert EntityDescriptionKey.SUNROOF not in keys
    assert EntityDescriptionKey.ONLINE not in keys
    assert EntityDescriptionKey.FUEL not in keys
    assert EntityDescriptionKey.FIRMWARE_VERSION not in keys

    # a permission error is not incapacity, so the front trunk stays
    assert "closure-fronttrunk" not in coordinator.incapable_codes
    assert EntityDescriptionKey.FRONT_TRUNK in keys

    # and a signal the vehicle answers is untouched
    assert EntityDescriptionKey.BATTERY_LEVEL in keys


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_capability_read_failure_creates_everything(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """A failed capability read must not delete the user's entities.

    Not knowing is different from knowing the vehicle cannot. When the read
    fails the integration falls back to the previous behaviour and creates
    every entity the granted scopes allow.
    """
    with patch(
        "custom_components.smartcar.coordinator.util.async_request_with_retry",
        side_effect=ClientError("boom"),
    ):
        await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    assert coordinator.incapable_codes == frozenset()
    assert EntityDescriptionKey.SUNROOF in _registry_keys(
        hass, entity_registry, mock_config_entry
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_capability_read_skipped_when_polling_disabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """Disabling polling means no requests of our own, including this one."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, pref_disable_polling=True)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 0
    assert EntityDescriptionKey.SUNROOF in _registry_keys(
        hass, entity_registry, mock_config_entry
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_truncated_signals_response_is_reported(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A response claiming more signals than it returned is not silent.

    The endpoint documents no page parameter but its envelope advertises
    paging. If that ever starts truncating, dropping signals quietly would look
    like entities going stale for no reason.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    caplog.clear()

    coordinator._merge_signal_data(
        {
            "data": [_signal("tractionbattery-stateofcharge")],
            "meta": {"totalCount": 999},
        }
    )

    assert "reported 999 signals but returned 1" in caplog.text


async def test_connections_are_read_across_pages() -> None:
    """Every page is read, because a partial list silently loses vehicles.

    The endpoint defaults to ten per page, and reading only the first page
    would also make the single-user check decide on a partial set.
    """
    auth = AsyncMock()
    pages = [
        {
            "data": [{"id": f"c{index}"} for index in range(100)],
            "meta": {"totalCount": 150},
        },
        {
            "data": [{"id": f"c{100 + index}"} for index in range(50)],
            "meta": {"totalCount": 150},
        },
    ]
    auth.request_v3.return_value.json.side_effect = pages

    connections = await _fetch_all_connections(auth)

    assert len(connections) == 150
    assert auth.request_v3.await_count == 2
    assert auth.request_v3.await_args_list[1].kwargs["params"] == {
        "page[number]": 2,
        "page[size]": 100,
    }


async def test_connection_paging_stops_at_a_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A service that always claims more pages cannot spin this forever."""

    class _Response:
        status = 200

        @staticmethod
        def raise_for_status() -> None:
            """Succeed."""

        @staticmethod
        async def json() -> dict:
            """Always claim there is another page.

            Returns:
                A page that never completes the total.
            """
            return {"data": [{"id": "c1"}], "meta": {"totalCount": 10_000}}

    calls = 0

    class _Auth:
        @staticmethod
        async def request_v3(_method: str, _path: str, **_kwargs) -> _Response:  # noqa: ANN003
            """Count the call.

            Returns:
                The canned response.
            """
            nonlocal calls
            calls += 1
            return _Response()

    connections = await _fetch_all_connections(cast("AbstractAuth", _Auth()))

    assert calls == CONNECTIONS_PAGE_LIMIT
    assert len(connections) == CONNECTIONS_PAGE_LIMIT
    assert "Stopped reading connections after" in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (HTTPStatus.ACCEPTED, {"type": "VEHICLE_STATE", "code": "UNREACHABLE"}),
        (HTTPStatus.OK, {"type": "VEHICLE_STATE", "code": "UNREACHABLE"}),
        (
            HTTPStatus.ACCEPTED,
            {"error": {"type": "UPSTREAM", "code": "NO_RESPONSE", "status": 502}},
        ),
    ],
    ids=["accepted", "ok_with_error_body", "nested_error"],
)
async def test_command_failure_in_the_body_is_a_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
    status: HTTPStatus,
    body: dict,
) -> None:
    """A command whose outcome arrives in the body is not reported as success.

    Commands taking longer than about 175 seconds return 202 with no
    Content-Length and stream the real result on the same connection. The
    status line is not the answer; the body is. Reading only the status made a
    failed slow command look like it worked.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = mock_config_entry.runtime_data.coordinators[vehicle["id"]]

    aioclient_mock.post(
        f"{MOCK_API_ENDPOINT}/vehicles/{vehicle['id']}/commands/security/lock",
        status=status,
        json=body,
    )

    with pytest.raises(SmartcarAPIError):
        await async_send_command(coordinator, "/security/lock", None)


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize(
    "body",
    [None, "", "not json", '"a string"', '{"meta": {"message": "ok"}}'],
    ids=["no_body", "empty", "not_json", "json_scalar", "no_error_fields"],
)
async def test_command_success_bodies_are_left_alone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
    body: str | None,
) -> None:
    """Anything that is not recognisably an error payload is a success."""
    await setup_integration(hass, mock_config_entry)

    coordinator = mock_config_entry.runtime_data.coordinators[vehicle["id"]]

    aioclient_mock.post(
        f"{MOCK_API_ENDPOINT}/vehicles/{vehicle['id']}/commands/security/lock",
        status=HTTPStatus.OK,
        text=body,
    )

    assert await async_send_command(coordinator, "/security/lock", None) is True


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_capability_check_is_quiet_unless_asked(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The capability check only explains itself when a caller wants it.

    Platform setup passes verbose so a hidden entity is explained once. Every
    other caller would otherwise repeat the same line on each update.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    caplog.clear()

    assert coordinator.is_datapoint_capable(EntityDescriptionKey.SUNROOF) is False
    assert "not capable" not in caplog.text

    assert (
        coordinator.is_datapoint_capable(EntityDescriptionKey.SUNROOF, verbose=True)
        is False
    )
    assert "not capable" in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v2"])
async def test_v2_batch_response_must_carry_responses(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A v2 batch body with no responses fails the update rather than passing.

    The v2 batch endpoint answers with a `responses` array. A body without one
    is not an empty update, it is an unusable reply, and treating it as data
    would leave every entity silently stale.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    with pytest.raises(UpdateFailed):
        coordinator._merge_response_data({"unexpected": True})


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_v3_update_merges_signals(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A v3 update body is read as signals, not as a v2 batch."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    merged = coordinator._merge_response_data(
        {"data": [_signal("tractionbattery-stateofcharge")]}
    )

    assert "tractionbattery-stateofcharge" in merged
