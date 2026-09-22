"""Tests for the permission list coming from Smartcar rather than from a form.

The config flow used to ask the user to tick permission boxes and send them as
the `scope` parameter on the Connect URL. A scope there overrides the Vehicle
Access configuration in the Smartcar dashboard, so the checkboxes quietly beat
the one screen where the user can see and change what they are granting, and
the answer was a request rather than a fact either way. Smartcar reports what
it actually granted on `GET /connections`, which is an application level read
and is not billed against any vehicle's allowance.
"""

from typing import Any
from unittest.mock import AsyncMock

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    load_json_object_fixture,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartcar import async_migrate_entry
from custom_components.smartcar.const import DOMAIN

from . import MOCK_API_ENDPOINT, setup_integration

GRANTED = [
    "control_charge",
    "read_battery",
    "read_charge",
    "read_location",
    "read_odometer",
    "read_security",
    "read_vehicle_info",
    "read_vin",
]


def _legacy_entry(vehicle_attributes: dict, expires_at: int) -> MockConfigEntry:
    """An entry from before the permission list was Smartcar's answer.

    Returns:
        A 2.1 entry whose stored scopes are the boxes the user ticked.
    """
    vehicle = dict(vehicle_attributes)
    vehicle_id = vehicle.pop("id")

    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=vehicle_id,
        version=2,
        minor_version=1,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "mock-access-token",
                "refresh_token": "mock-refresh-token",
                "expires_at": expires_at,
                "scopes": ["read_vehicle_info", "read_vin", "read_tires"],
            },
            "vehicles": {vehicle_id: vehicle},
        },
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_an_older_entry_is_brought_onto_the_granted_list(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    vehicle_attributes: dict,
    expires_at: int,
    mock_smartcar_auth: AsyncMock,
    vehicle: dict,
) -> None:
    """An entry that stored a request is migrated onto Smartcar's answer."""
    aioclient_mock.get(
        f"{MOCK_API_ENDPOINT}/connections",
        json=load_json_object_fixture("api/list_connections.json", DOMAIN),
    )
    entry = _legacy_entry(vehicle_attributes, expires_at)

    await setup_integration(hass, entry)

    assert entry.minor_version == 2
    assert entry.data["granted_permissions"] == GRANTED
    # read_tires was asked for and never granted, so it is gone
    assert entry.data["token"]["scopes"] == GRANTED


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_a_failed_read_leaves_the_entry_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    vehicle_attributes: dict,
    expires_at: int,
    mock_smartcar_auth: AsyncMock,
    vehicle: dict,
) -> None:
    """Smartcar being unreachable must not rewrite what the entry knows.

    Home Assistant retries the setup, and the previously requested list keeps
    working until one of those attempts succeeds.
    """
    aioclient_mock.get(f"{MOCK_API_ENDPOINT}/connections", exc=ClientError("boom"))
    entry = _legacy_entry(vehicle_attributes, expires_at)

    await setup_integration(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.minor_version == 1
    assert "granted_permissions" not in entry.data


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_a_v2_entry_keeps_what_it_asked_for(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    vehicle_attributes: dict,
    expires_at: int,
    mock_smartcar_auth: AsyncMock,
    vehicle: dict,
) -> None:
    """v2 has no `/connections`, so the requested list is the only one there is."""
    entry = _legacy_entry(vehicle_attributes, expires_at)

    await setup_integration(hass, entry)

    assert entry.minor_version == 2
    assert "granted_permissions" not in entry.data
    assert entry.data["token"]["scopes"] == [
        "read_vehicle_info",
        "read_vin",
        "read_tires",
    ]


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_a_current_entry_is_not_migrated_again(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
) -> None:
    """An entry already at the current version asks Smartcar nothing.

    Home Assistant only calls the migration when the version differs, so this
    calls it directly: the guard has to hold on its own.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=mock_config_entry.unique_id,
        version=2,
        minor_version=2,
        data=dict(mock_config_entry.data),
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 2
    assert not [
        call for call in aioclient_mock.mock_calls if "connections" in str(call[1])
    ]


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_the_granted_list_gates_the_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Entity gating reads the granted list, wherever it came from."""
    data: dict[str, Any] = {**mock_config_entry.data}
    data["granted_permissions"] = ["read_vehicle_info", "read_vin", "read_odometer"]
    data["token"] = {**data["token"], "scopes": data["granted_permissions"]}
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data=data)

    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    assert coordinator.is_scope_enabled("odometer")
    assert not coordinator.is_scope_enabled("battery_level")
