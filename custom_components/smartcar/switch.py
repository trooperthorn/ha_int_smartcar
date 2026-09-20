from dataclasses import dataclass
import logging

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EntityDescriptionKey
from .coordinator import SmartcarVehicleCoordinator
from .entity import SmartcarEntity, SmartcarEntityDescription

_LOGGER = logging.getLogger(__name__)

# commands are serialised: the vehicle's monthly allowance is shared with
# reads, and two commands racing to the same car is not something the OEM
# handles gracefully.
PARALLEL_UPDATES = 1


def _hvac_bool(body: object) -> object:
    """Extract a boolean from an HVAC webhook signal body ({"value": bool}).

    Returns:
        The extracted boolean or the body unchanged.
    """
    if isinstance(body, dict):
        return body.get("value")
    return body


@dataclass(frozen=True, kw_only=True)
class SmartcarSwitchDescription(SwitchEntityDescription, SmartcarEntityDescription):
    """Class describing Smartcar switch entities."""


ENTITY_DESCRIPTIONS: tuple[SwitchEntityDescription, ...] = (
    SmartcarSwitchDescription(
        key=EntityDescriptionKey.CHARGING,
        name="Charging",
        value_key_path="charge-ischarging.value",
        icon="mdi:ev-plug-type2",
    ),
)

CLIMATE_ENTITY_DESCRIPTIONS: tuple[SwitchEntityDescription, ...] = (
    SmartcarSwitchDescription(
        key=EntityDescriptionKey.CLIMATE,
        name="Climate",
        value_key_path="hvac-iscabinhvacactive",
        value_cast=_hvac_bool,
        icon="mdi:air-conditioner",
    ),
)


async def async_setup_entry(  # noqa: RUF029
    hass: HomeAssistant,  # noqa: ARG001
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches from coordinator."""
    coordinators: dict[str, SmartcarVehicleCoordinator] = (
        entry.runtime_data.coordinators
    )
    entities: list[SwitchEntity] = [
        SmartcarChargingSwitch(coordinator, description)
        for coordinator in coordinators.values()
        for description in ENTITY_DESCRIPTIONS
        if coordinator.is_entity_supported(description.key, verbose=True)
    ]
    # the climate switch is v2 only. Smartcar publishes no climate command on
    # v3: not in the OpenAPI document, not in the API reference, and not as a
    # column in the per-vehicle compatibility matrix, which lists all eleven
    # commands that do exist. The only climate commands that ever shipped were
    # make-specific v2 ones. Creating this entity on a v3 entry hands the user
    # a control whose every press posts to a path that does not exist.
    # See docs/api-reference.md, "The climate command does not exist".
    entities += [
        SmartcarClimateSwitch(coordinator, description)
        for coordinator in coordinators.values()
        for description in CLIMATE_ENTITY_DESCRIPTIONS
        if coordinator.version == "v2"
        and coordinator.is_entity_supported(description.key, verbose=True)
    ]
    _LOGGER.info("Adding %s Smartcar switch entities", len(entities))
    async_add_entities(entities)


class SmartcarChargingSwitch(SmartcarEntity[bool, bool], SwitchEntity):
    """Switch entity."""

    _attr_has_entity_name = True

    @property
    def is_on(self) -> bool:
        return self._extract_value()

    async def async_turn_on(
        self,
        **kwargs: object,  # noqa: ARG002
    ) -> None:
        version = self.coordinator.auth.version
        command = "/charge/start"
        payload = None

        if version == "v2":
            command = "/charge"
            payload = {"action": "START"}

        await self._async_send_command(command, payload)
        self._inject_raw_value(value=True)
        self.async_write_ha_state()

    async def async_turn_off(
        self,
        **kwargs: object,  # noqa: ARG002
    ) -> None:
        version = self.coordinator.auth.version
        command = "/charge/stop"
        payload = None

        if version == "v2":
            command = "/charge"
            payload = {"action": "STOP"}

        await self._async_send_command(command, payload)
        self._inject_raw_value(value=False)
        self.async_write_ha_state()


class SmartcarClimateSwitch(SmartcarEntity[bool, bool], SwitchEntity):
    """Switch entity to start/stop cabin climate (preconditioning).

    v2 only. There is no v3 climate command to send, so this entity is not
    created for a v3 entry and the v3 command path it used to build has been
    removed rather than left as unreachable code.
    """

    _attr_has_entity_name = True

    @property
    def is_on(self) -> bool:
        return self._extract_value()

    async def async_turn_on(
        self,
        **kwargs: object,  # noqa: ARG002
    ) -> None:
        await self._async_send_command("/climate", {"action": "START"})
        self._inject_raw_value(value=True)
        self.async_write_ha_state()

    async def async_turn_off(
        self,
        **kwargs: object,  # noqa: ARG002
    ) -> None:
        await self._async_send_command("/climate", {"action": "STOP"})
        self._inject_raw_value(value=False)
        self.async_write_ha_state()
