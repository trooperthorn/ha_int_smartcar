"""Tests for entities arriving switched on when the vehicle can answer them.

The static list of default-enabled entities was written for an average car. On
a real 2025 US ID. Buzz it left thirty seven entities switched off that the
vehicle answers perfectly well, while switching on diagnostics it has never
heard of. The vehicle already says what it can do in every signals response,
so that is what decides now.

`tests/fixtures/api/vw_id_4.id_buzz.v3.json` is that vehicle's store: twenty
five signals, of which six carry COMPATIBILITY / VEHICLE_NOT_CAPABLE and three
carry VEHICLE_STATE / NOT_CHARGING. The error signals are reproduced from the
live debug log of 2026-09-22; the successful bodies are representative shapes
for the signals that log proves were present.
"""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartcar.const import DOMAIN, EntityDescriptionKey, Scope
from custom_components.smartcar.coordinator import SmartcarVehicleCoordinator

from . import setup_integration

# the nine scopes Sean's Smartcar connection actually carries. no diagnostics,
# no climate, no fuel, no tires, so those entities are never created at all.
ID_BUZZ_SCOPES = [
    Scope.READ_VEHICLE_INFO,
    Scope.READ_VIN,
    Scope.READ_BATTERY,
    Scope.READ_CHARGE,
    Scope.READ_LOCATION,
    Scope.READ_ODOMETER,
    Scope.READ_SECURITY,
    Scope.CONTROL_CHARGE,
    Scope.CONTROL_SECURITY,
]

# every entity the store proves this vehicle answers, including the three that
# came back with VEHICLE_STATE / NOT_CHARGING: "not charging" is a reading, not
# a defect, and those entities work the moment the car is plugged in.
EXPECTED_ENABLED = {
    EntityDescriptionKey.BATTERY_CAPACITY,
    EntityDescriptionKey.BATTERY_LEVEL,
    EntityDescriptionKey.CHARGE_CHARGERATE,
    EntityDescriptionKey.CHARGE_LIMIT,
    EntityDescriptionKey.CHARGE_TIMETOCOMPLETE,
    EntityDescriptionKey.CHARGE_TIME_TO_COMPLETE,
    EntityDescriptionKey.CHARGE_WATTAGE,
    EntityDescriptionKey.CHARGING,
    EntityDescriptionKey.CHARGING_STATE,
    EntityDescriptionKey.DOOR_BACK_LEFT,
    EntityDescriptionKey.DOOR_BACK_LEFT_LOCK,
    EntityDescriptionKey.DOOR_BACK_RIGHT,
    EntityDescriptionKey.DOOR_BACK_RIGHT_LOCK,
    EntityDescriptionKey.DOOR_FRONT_LEFT,
    EntityDescriptionKey.DOOR_FRONT_LEFT_LOCK,
    EntityDescriptionKey.DOOR_FRONT_RIGHT,
    EntityDescriptionKey.DOOR_FRONT_RIGHT_LOCK,
    EntityDescriptionKey.DOOR_LOCK,
    EntityDescriptionKey.ENGINE_COVER,
    EntityDescriptionKey.FRONT_TRUNK,
    EntityDescriptionKey.FRONT_TRUNK_LOCK,
    EntityDescriptionKey.LOCATION,
    EntityDescriptionKey.ODOMETER,
    EntityDescriptionKey.PLUG_STATUS,
    EntityDescriptionKey.RANGE,
    EntityDescriptionKey.REAR_TRUNK,
    EntityDescriptionKey.REAR_TRUNK_LOCK,
    EntityDescriptionKey.WINDOW_BACK_LEFT,
    EntityDescriptionKey.WINDOW_BACK_RIGHT,
    EntityDescriptionKey.WINDOW_FRONT_LEFT,
    EntityDescriptionKey.WINDOW_FRONT_RIGHT,
}

# the meta entities count nothing the vehicle knows about, so they are always
# on: without them there is no way to see the allowance being spent.
EXPECTED_META = {
    EntityDescriptionKey.API_CALLS_REMAINING,
    EntityDescriptionKey.API_CALLS_USED,
    EntityDescriptionKey.LAST_WEBHOOK_RECEIVED,
    EntityDescriptionKey.NEXT_SCHEDULED_POLL,
}


def _keys_by_state(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry
) -> tuple[set[str], set[str]]:
    """Split this entry's entities into enabled and disabled keys.

    Returns:
        The enabled keys and the disabled keys.
    """
    enabled: set[str] = set()
    disabled: set[str] = set()

    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        _, key = registry_entry.unique_id.split("_", 1)
        (disabled if registry_entry.disabled_by is not None else enabled).add(key)

    return enabled, disabled


def _coordinator(entry: MockConfigEntry) -> SmartcarVehicleCoordinator:
    """The single vehicle's coordinator.

    Returns:
        The coordinator.
    """
    coordinator: SmartcarVehicleCoordinator = next(
        iter(entry.runtime_data.coordinators.values())
    )
    return coordinator


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize("api_response_type", ["id_buzz"])
@pytest.mark.parametrize("enabled_scopes", [ID_BUZZ_SCOPES])
async def test_every_answered_signal_arrives_enabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """The ID. Buzz gets every entity it can fill, switched on."""
    await setup_integration(hass, mock_config_entry)

    enabled, disabled = _keys_by_state(entity_registry, mock_config_entry)

    assert enabled == {key.value for key in EXPECTED_ENABLED | EXPECTED_META}

    # a signal the store never mentioned is still created, because the store is
    # a cache of what webhooks collected rather than a capability list, but it
    # has nothing to show yet so it stays out of the way.
    assert EntityDescriptionKey.CHARGE_VOLTAGE.value in disabled
    assert EntityDescriptionKey.FIRMWARE_VERSION.value in disabled

    # COMPATIBILITY errors still mean no entity at all, enabled or otherwise.
    assert EntityDescriptionKey.SUNROOF.value not in enabled | disabled
    assert EntityDescriptionKey.ONLINE.value not in enabled | disabled


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize("api_response_type", ["id_buzz"])
@pytest.mark.parametrize("enabled_scopes", [ID_BUZZ_SCOPES])
async def test_a_signal_that_shows_up_later_enables_its_entity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """A vehicle that starts answering a signal gets the entity switched on.

    A webhook data list that grows, or a car that was asleep when the store was
    read, should not need the user to go hunting through the entity list.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)
    entity_id = entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{coordinator.vehicle_id}_{EntityDescriptionKey.CHARGE_VOLTAGE}",
    )
    assert entity_id is not None
    assert (before := entity_registry.async_get(entity_id)) is not None
    assert before.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    coordinator.async_set_updated_data(
        {**coordinator.data, "charge-voltage": {"value": 240}}
    )
    await hass.async_block_till_done()

    assert (after := entity_registry.async_get(entity_id)) is not None
    assert after.disabled_by is None


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize("api_response_type", ["id_buzz"])
@pytest.mark.parametrize("enabled_scopes", [ID_BUZZ_SCOPES])
async def test_an_entity_the_user_switched_off_stays_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """Turning an entity back on against the user's wishes is not a feature."""
    await setup_integration(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)
    entity_id = entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{coordinator.vehicle_id}_{EntityDescriptionKey.ODOMETER}",
    )
    assert entity_id is not None

    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    coordinator.async_set_updated_data(dict(coordinator.data))
    await hass.async_block_till_done()

    assert (entry := entity_registry.async_get(entity_id)) is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.USER


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize("api_response_type", ["id_buzz"])
@pytest.mark.parametrize("enabled_scopes", [ID_BUZZ_SCOPES])
async def test_other_registry_entries_are_left_alone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    vehicle: dict,
) -> None:
    """Only this vehicle's own datapoint entities are ever enabled.

    One config entry can hold several vehicles, and it also holds meta entities
    whose unique id suffix is not a signal at all.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)
    strangers = [
        entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=mock_config_entry,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        ).entity_id
        for unique_id in (
            f"another-vehicle_{EntityDescriptionKey.ODOMETER}",
            f"{coordinator.vehicle_id}_not_a_datapoint",
        )
    ]

    coordinator.async_set_updated_data(dict(coordinator.data))
    await hass.async_block_till_done()

    for entity_id in strangers:
        assert (entry := entity_registry.async_get(entity_id)) is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize("api_response_type", ["id_buzz"])
@pytest.mark.parametrize("enabled_scopes", [ID_BUZZ_SCOPES])
async def test_a_scope_that_was_not_granted_is_never_enabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A signal we may not ask for cannot be one the vehicle answers."""
    await setup_integration(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)

    assert not coordinator.is_entity_enabled_by_default(
        EntityDescriptionKey.DIAG_ABS, static_default=True
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_v2_keeps_the_static_list(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """v2 has no signal codes, so there is nothing to ask the vehicle.

    The hand written list is the answer whenever nothing has been read, which
    is also what a failed capability read falls back to.
    """
    await setup_integration(hass, mock_config_entry)

    coordinator = _coordinator(mock_config_entry)

    for static_default in (True, False):
        assert (
            coordinator.is_entity_enabled_by_default(
                EntityDescriptionKey.BATTERY_LEVEL, static_default=static_default
            )
            is static_default
        )


def test_no_data_yet_falls_back_to_the_static_list(
    hass: HomeAssistant,
) -> None:
    """A capability read that never answered must not hide anything.

    Constructed rather than set up, because "the coordinator exists and has
    read nothing" is exactly the state under test.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={"token": {"scopes": list(Scope)}})
    entry.add_to_hass(hass)
    coordinator = SmartcarVehicleCoordinator(
        hass,
        auth=_UnusedAuth(),
        vehicle_id="vehicle-id",
        vin="vin",
        entry=entry,
        version="v3",
    )

    assert coordinator.is_entity_enabled_by_default(
        EntityDescriptionKey.CHARGE_VOLTAGE, static_default=True
    )
    coordinator._async_enable_newly_answered_entities()


class _UnusedAuth:
    """Stands in for the auth object the coordinator never calls here."""

    version = "v3"

    def __getattr__(self, name: str) -> Any:
        """Fail loudly if this test ever starts making requests.

        Raises:
            AssertionError: Always.
        """
        msg = f"no request expected, got {name}"
        raise AssertionError(msg)
