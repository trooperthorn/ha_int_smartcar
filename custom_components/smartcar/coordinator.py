from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
import copy
from dataclasses import dataclass
import datetime as dt
from datetime import timedelta
from http import HTTPStatus
import logging
import numbers
from typing import Any, Literal

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from . import util
from .auth import AbstractAuth
from .budget import DEFAULT_MONTHLY_BUDGET, DEFAULT_RESERVE, ApiBudget
from .cache import SignalCache
from .const import (
    CONF_APPLICATION_MANAGEMENT_TOKEN,
    CONF_BUDGET_RESERVE,
    CONF_MONTHLY_BUDGET,
    DOMAIN,
    EntityDescriptionKey,
)
from .polling import resolve_settings
from .types import APIVersion
from .util import key_path_get, key_path_update

_LOGGER = logging.getLogger(__name__)

# values from the smartcar service that denote an imperial measurement and can
# be converted by one of the imperial_conversion functions defined on an entity
# description.
_IMPERIAL_MEASUREMENTS = {"miles", "psi", "gallons"}


_SIGNAL_BODY_MULTIVALUE_ITEM_KEY_MAP: dict[str | None, str] = {
    "charge-chargelimits": "limit",
}

VEHICLE_FRONT_ROW = 0
VEHICLE_BACK_ROW = 1
VEHICLE_LEFT_COLUMN = 0
VEHICLE_RIGHT_COLUMN = 1

UPDATE_INTERVAL = timedelta(hours=6)

# Signal error conditions that are structural or otherwise expected for a given
# vehicle and therefore recur on every update (e.g. a vehicle that is simply not
# capable of a signal, or charge signals while not charging). Logging these at
# ERROR floods the log with permanent noise, so they are demoted to DEBUG.
# Genuine/actionable errors keep their original level.
_BENIGN_SIGNAL_ERRORS: frozenset[tuple[str | None, str | None]] = frozenset(
    {
        ("COMPATIBILITY", "VEHICLE_NOT_CAPABLE"),
        ("VEHICLE_STATE", "NOT_CHARGING"),
    }
)


@dataclass
class DatapointConfig:
    """Datapoint config class."""

    code: str | None  # none indicates no v3 equivalent
    required_scopes: list[str]
    endpoint_v2: str | None  # the read (and batch) endpoint
    value_key_path_v2: str | None
    value_transform_v2: Callable[[Any], Any] = lambda x: {"value": x}
    value_merge_v2: Callable[[dict, dict], dict] = lambda current, update: (
        current | update
    )
    is_v2_value: Callable[[Any], bool] = (  # for saved restore-state values
        lambda _x: False
    )

    @property
    def storage_key(self) -> str:
        assert self.code
        return self.code

    @property
    def storage_key_v2(self) -> str:
        endpoint_v2 = self.endpoint_v2
        assert endpoint_v2 is not None
        return endpoint_v2.strip("/").replace("/", "_")


def _tire_pressure_merge_v2(current: dict, update: dict) -> dict:
    values = []
    seen = set()

    for value in (*update.get("values", []), *current.get("values", [])):
        key = (value["row"], value["column"])
        if key not in seen:
            values.append(value)
            seen.add(key)

    return update | {"values": values}


def _is_v2_value_if_numeric(
    value: Any,  # noqa: ANN401
) -> bool:
    return isinstance(value, numbers.Number)


def _is_v2_value_if_string(
    value: Any,  # noqa: ANN401
) -> bool:
    return isinstance(value, str)


DATAPOINT_ENTITY_KEY_MAP = {
    EntityDescriptionKey.BATTERY_CAPACITY: DatapointConfig(
        "tractionbattery-nominalcapacity",
        ["read_battery"],
        "/battery/nominal_capacity",
        "capacity.nominal",
        lambda nominal: {"capacity": nominal, "availableCapacities": []},
    ),
    EntityDescriptionKey.BATTERY_LEVEL: DatapointConfig(
        "tractionbattery-stateofcharge",
        ["read_battery"],
        "/battery",
        "percentRemaining",
    ),
    EntityDescriptionKey.CHARGE_CHARGERATE: DatapointConfig(
        "charge-chargerate",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_ENERGYADDED: DatapointConfig(
        "charge-energyadded",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_TIMETOCOMPLETE: DatapointConfig(
        "charge-timetocomplete",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.BATTERY_HEATER_ACTIVE: DatapointConfig(
        "tractionbattery-isheateractive",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_LIMIT: DatapointConfig(
        "charge-chargelimits",
        ["read_charge", "control_charge"],
        "/charge/limit",
        "limit",
        lambda limit: {
            "values": [{"type": "global", "limit": limit, "condition": None}]
        },
        is_v2_value=_is_v2_value_if_numeric,  # for saved restore-state values
    ),
    EntityDescriptionKey.CHARGING: DatapointConfig(  # for the switch
        "charge-ischarging",
        ["read_charge", "control_charge"],
        "/charge",
        "state",
        lambda state: {"value": state == "CHARGING" if state is not None else None},
        is_v2_value=_is_v2_value_if_string,  # for saved restore-state values
    ),
    EntityDescriptionKey.CHARGING_STATE: DatapointConfig(
        "charge-detailedchargingstatus",
        ["read_charge", "control_charge"],
        "/charge",
        "state",
    ),
    EntityDescriptionKey.DOOR_LOCK: DatapointConfig(
        "closure-islocked",
        ["read_security", "control_security"],
        "/security",
        "isLocked",
    ),
    EntityDescriptionKey.DOOR_BACK_LEFT_LOCK: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_BACK_RIGHT_LOCK: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_FRONT_LEFT_LOCK: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_FRONT_RIGHT_LOCK: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_BACK_LEFT: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_BACK_RIGHT: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_FRONT_LEFT: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DOOR_FRONT_RIGHT: DatapointConfig(
        "closure-doors",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.ENGINE_OIL: DatapointConfig(
        "internalcombustionengine-oillife",
        ["read_engine_oil"],
        "/engine/oil",
        "lifeRemaining",
    ),
    EntityDescriptionKey.ENGINE_COVER: DatapointConfig(
        "closure-enginecover",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.FUEL: DatapointConfig(
        "internalcombustionengine-amountremaining",
        ["read_fuel"],
        "/fuel",
        "amountRemaining",
    ),
    EntityDescriptionKey.FUEL_PERCENT: DatapointConfig(
        "internalcombustionengine-fuellevel",
        ["read_fuel"],
        "/fuel",
        "percentRemaining",
    ),
    EntityDescriptionKey.FUEL_RANGE: DatapointConfig(
        "internalcombustionengine-range",
        ["read_fuel"],
        "/fuel",
        "range",
    ),
    EntityDescriptionKey.FRONT_TRUNK: DatapointConfig(
        "closure-fronttrunk",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.FRONT_TRUNK_LOCK: DatapointConfig(
        "closure-fronttrunk",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.SUNROOF: DatapointConfig(
        "closure-sunroof",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.LOCATION: DatapointConfig(
        "location-preciselocation",
        ["read_location"],
        "/location",
        None,
        lambda location: location,
    ),
    EntityDescriptionKey.LOW_VOLTAGE_BATTERY_LEVEL: DatapointConfig(
        "lowvoltagebattery-stateofcharge",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.ODOMETER: DatapointConfig(
        "odometer-traveleddistance", ["read_odometer"], "/odometer", "distance"
    ),
    EntityDescriptionKey.PLUG_STATUS: DatapointConfig(
        "charge-ischargingcableconnected",
        ["read_charge"],
        "/charge",
        "isPluggedIn",
    ),
    EntityDescriptionKey.RANGE: DatapointConfig(
        "tractionbattery-range",
        ["read_battery"],
        "/battery",
        "range",
    ),
    EntityDescriptionKey.GEAR_STATE: DatapointConfig(
        "transmission-gearstate",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.TIRE_PRESSURE_BACK_LEFT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
        "/tires/pressure",
        "backLeft",
        lambda pressure: {
            "values": [
                {
                    "tirePressure": pressure,
                    "column": VEHICLE_LEFT_COLUMN,
                    "row": VEHICLE_BACK_ROW,
                }
            ],
            "rowCount": 2,
            "columnCount": 2,
        },
        _tire_pressure_merge_v2,
        _is_v2_value_if_numeric,  # for saved restore-state values
    ),
    EntityDescriptionKey.TIRE_PRESSURE_BACK_RIGHT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
        "/tires/pressure",
        "backRight",
        lambda pressure: {
            "values": [
                {
                    "tirePressure": pressure,
                    "column": VEHICLE_RIGHT_COLUMN,
                    "row": VEHICLE_BACK_ROW,
                }
            ],
            "rowCount": 2,
            "columnCount": 2,
        },
        _tire_pressure_merge_v2,
        _is_v2_value_if_numeric,  # for saved restore-state values
    ),
    EntityDescriptionKey.TIRE_PRESSURE_FRONT_LEFT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
        "/tires/pressure",
        "frontLeft",
        lambda pressure: {
            "values": [
                {
                    "tirePressure": pressure,
                    "column": VEHICLE_LEFT_COLUMN,
                    "row": VEHICLE_FRONT_ROW,
                }
            ],
            "rowCount": 2,
            "columnCount": 2,
        },
        _tire_pressure_merge_v2,
        _is_v2_value_if_numeric,  # for saved restore-state values
    ),
    EntityDescriptionKey.TIRE_PRESSURE_FRONT_RIGHT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
        "/tires/pressure",
        "frontRight",
        lambda pressure: {
            "values": [
                {
                    "tirePressure": pressure,
                    "column": VEHICLE_RIGHT_COLUMN,
                    "row": VEHICLE_FRONT_ROW,
                }
            ],
            "rowCount": 2,
            "columnCount": 2,
        },
        _tire_pressure_merge_v2,
        _is_v2_value_if_numeric,  # for saved restore-state values
    ),
    EntityDescriptionKey.WINDOW_BACK_LEFT: DatapointConfig(
        "closure-windows",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.WINDOW_BACK_RIGHT: DatapointConfig(
        "closure-windows",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.WINDOW_FRONT_LEFT: DatapointConfig(
        "closure-windows",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.WINDOW_FRONT_RIGHT: DatapointConfig(
        "closure-windows",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.REAR_TRUNK: DatapointConfig(
        "closure-reartrunk",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.REAR_TRUNK_LOCK: DatapointConfig(
        "closure-reartrunk",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.ONLINE: DatapointConfig(
        "connectivitystatus-isonline",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.ASLEEP: DatapointConfig(
        "connectivitystatus-isasleep",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.DIGITAL_KEY_PAIRED: DatapointConfig(
        "connectivitystatus-isdigitalkeypaired",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.SURVEILLANCE_ENABLED: DatapointConfig(
        "surveillance-isenabled",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_VOLTAGE: DatapointConfig(
        "charge-voltage",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_AMPERAGE: DatapointConfig(
        "charge-amperage",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_WATTAGE: DatapointConfig(
        "charge-wattage",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_TIME_TO_COMPLETE: DatapointConfig(
        "charge-timetocomplete",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_AMPERAGE_MAX: DatapointConfig(
        "charge-amperagemax",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.CHARGE_FAST_CHARGER_PRESENT: DatapointConfig(
        "charge-isfastchargerpresent",
        [],
        None,
        None,
    ),
    EntityDescriptionKey.FIRMWARE_VERSION: DatapointConfig(
        "connectivitysoftware-currentfirmwareversion",
        [],
        None,
        None,
    ),
    # Diagnostics (webhook-only, require read_diagnostics)
    EntityDescriptionKey.DIAG_ABS: DatapointConfig(
        "diagnostics-abs", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_MIL: DatapointConfig(
        "diagnostics-mil", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_DTC_COUNT: DatapointConfig(
        "diagnostics-dtccount", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_DTC_LIST: DatapointConfig(
        "diagnostics-dtclist", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_EV_BATTERY_CONDITIONING: DatapointConfig(
        "diagnostics-evbatteryconditioning", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_EV_CHARGING: DatapointConfig(
        "diagnostics-evcharging", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_EV_DRIVE_UNIT: DatapointConfig(
        "diagnostics-evdriveunit", ["read_diagnostics"], None, None
    ),
    EntityDescriptionKey.DIAG_EV_HV_BATTERY: DatapointConfig(
        "diagnostics-evhvbattery", ["read_diagnostics"], None, None
    ),
    # Climate status (webhook-only, require read_climate)
    EntityDescriptionKey.CABIN_TARGET_TEMPERATURE: DatapointConfig(
        "hvac-cabintargettemperature", ["read_climate"], None, None
    ),
    EntityDescriptionKey.IS_CABIN_HVAC_ACTIVE: DatapointConfig(
        "hvac-iscabinhvacactive", ["read_climate"], None, None
    ),
    EntityDescriptionKey.IS_FRONT_DEFROSTER_ACTIVE: DatapointConfig(
        "hvac-isfrontdefrosteractive", ["read_climate"], None, None
    ),
    EntityDescriptionKey.IS_REAR_DEFROSTER_ACTIVE: DatapointConfig(
        "hvac-isreardefrosteractive", ["read_climate"], None, None
    ),
    EntityDescriptionKey.IS_STEERING_HEATER_ACTIVE: DatapointConfig(
        "hvac-issteeringheateractive", ["read_climate"], None, None
    ),
    # Climate control switch (reads HVAC-active state, requires control_climate)
    EntityDescriptionKey.CLIMATE: DatapointConfig(
        "hvac-iscabinhvacactive", ["read_climate", "control_climate"], None, None
    ),
}

DATAPOINT_STORAGE_KEY_V2_MAP = {
    storage_key_v2: tuple(
        datapoint
        for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()
        if datapoint.endpoint_v2 is not None
        and datapoint.storage_key_v2 == storage_key_v2
    )
    for storage_key_v2 in {
        datapoint.storage_key_v2
        for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()
        if datapoint.endpoint_v2 is not None
    }
}

DATAPOINT_CODE_MAP = {
    code: tuple(
        datapoint
        for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()
        if datapoint.code == code
    )
    for code in {datapoint.code for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()}
}


class SmartcarVehicleCoordinator(DataUpdateCoordinator):
    """Coordinates updates with selective batch paths and dynamic interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        auth: AbstractAuth,
        vehicle_id: str,
        vin: str,
        entry: ConfigEntry,
        version: APIVersion,
        budget: ApiBudget | None = None,
        cache: SignalCache | None = None,
    ) -> None:
        """Initialize coordinator."""
        self.auth = auth
        self.vehicle_id = vehicle_id
        self.vin = vin
        self.entry = entry
        self.version = version
        self.batch_requests: set[EntityDescriptionKey] = set()
        self.data: dict[str, Any] = {}
        # signal codes the vehicle has positively reported it cannot answer.
        # empty means unknown, which is treated as capable.
        self.incapable_codes: frozenset[str] = frozenset()
        self.budget = budget
        self.cache = cache
        self.settings = resolve_settings(dict(entry.options))
        self._event_polls_today = 0
        self._event_poll_day: dt.date | None = None
        self._live_webhook_seen = False
        # the most recent poll's shape, kept for diagnostics: what the store
        # actually held, and which codes it returned that map to no entity.
        self.last_poll_total_count: int | None = None
        self.last_poll_unmapped_codes: list[str] = []

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{vehicle_id}",
            update_interval=self._resolve_update_interval(),
        )

    def _resolve_update_interval(self) -> timedelta | None:
        """Decide the scheduled cadence for this vehicle.

        A webhook makes scheduled polling redundant, so a configured management
        token still wins: that is the cheapest and freshest source there is.
        Otherwise the user's chosen profile decides.

        Returns:
            The interval, or None when nothing should be scheduled.
        """
        if CONF_APPLICATION_MANAGEMENT_TOKEN in self.entry.data:
            return None

        return self.settings.scheduled_interval

    @property
    def monthly_budget(self) -> int:
        """The per-vehicle monthly call allowance.

        Returns:
            The configured allowance.
        """
        return int(self.entry.options.get(CONF_MONTHLY_BUDGET, DEFAULT_MONTHLY_BUDGET))

    @property
    def budget_reserve(self) -> int:
        """Calls kept back from polling so commands can always be sent.

        Returns:
            The configured reserve.
        """
        return int(self.entry.options.get(CONF_BUDGET_RESERVE, DEFAULT_RESERVE))

    def budget_allows_poll(self) -> bool:
        """Whether a scheduled or event driven poll may spend a call.

        Returns:
            True when there is allowance left above the reserve.
        """
        if self.budget is None:
            return True

        return self.budget.can_poll(
            self.vehicle_id, self.monthly_budget, self.budget_reserve
        )

    async def async_record_call(self, calls: int = 1) -> None:
        """Count calls made to this vehicle against its allowance."""
        if self.budget is not None:
            await self.budget.async_record(self.vehicle_id, calls)

    def _claim_event_poll(self) -> bool:
        """Take one of today's event driven polls, if any are left.

        A phone that flaps between home and away would otherwise drain a month
        of allowance in an afternoon, so the cap is enforced here rather than
        trusted to the trigger.

        Returns:
            True when the caller may poll.
        """
        today = dt_util.now().date()

        if self._event_poll_day != today:
            self._event_poll_day = today
            self._event_polls_today = 0

        if self._event_polls_today >= self.settings.daily_event_cap:
            _LOGGER.debug(
                "Coordinator %s: daily event poll cap of %s already reached",
                self.name,
                self.settings.daily_event_cap,
            )
            return False

        self._event_polls_today += 1

        return True

    async def async_request_event_poll(self, reason: str) -> bool:
        """Poll because something happened, rather than because time passed.

        Returns:
            True when a refresh was actually made.
        """
        if not self._claim_event_poll():
            return False

        if not self.budget_allows_poll():
            _LOGGER.warning(
                "Coordinator %s: skipping %s poll, only %s of %s calls left this "
                "period and %s are reserved for commands",
                self.name,
                reason,
                self.budget.remaining(self.vehicle_id, self.monthly_budget)
                if self.budget
                else "?",
                self.monthly_budget,
                self.budget_reserve,
            )
            return False

        _LOGGER.info("Coordinator %s: polling because %s", self.name, reason)
        await self.async_request_refresh()

        return True

    def is_scope_enabled(
        self, sensor_key: EntityDescriptionKey | str, *, verbose: bool = False
    ) -> bool:
        sensor_key = EntityDescriptionKey(sensor_key)
        token_scopes = self.entry.data.get("token", {}).get("scopes", [])
        required_scopes = DATAPOINT_ENTITY_KEY_MAP[sensor_key].required_scopes
        missing = [scope for scope in required_scopes if scope not in token_scopes]
        enabled = len(missing) == 0

        if not enabled and verbose:
            # a scope the user chose not to grant is a setting, not a problem,
            # and this fires once per affected entity on every setup and every
            # reload: twenty warnings for a correctly configured vehicle. the
            # information is still worth having, at a level that is asked for.
            _LOGGER.debug(
                "Skipping `%s` which requires %r, but "
                "user is missing %r with enabled scopes of %r.",
                sensor_key,
                required_scopes,
                missing,
                token_scopes,
            )

        return enabled

    def is_entity_supported(
        self, sensor_key: EntityDescriptionKey | str, *, verbose: bool = False
    ) -> bool:
        """Whether an entity should exist for this vehicle at all.

        Two independent questions. The scopes decide what the user let us ask
        for; the vehicle decides what it can answer. An entity needs both, and
        the second one was previously not asked, which is why a vehicle with no
        diagnostics still got five diagnostic entities that could never hold a
        value.

        Returns:
            True when the scope is granted and the vehicle has not told us it
            is incapable of the signal.
        """
        return self.is_scope_enabled(
            sensor_key, verbose=verbose
        ) and self.is_datapoint_capable(sensor_key, verbose=verbose)

    def is_datapoint_capable(
        self, sensor_key: EntityDescriptionKey | str, *, verbose: bool = False
    ) -> bool:
        """Whether the vehicle can answer the signal behind an entity.

        Capability is read from the vehicle itself rather than from a table:
        a signal the vehicle cannot produce comes back with a COMPATIBILITY
        error, which is structural and permanent, unlike VEHICLE_STATE or
        PERMISSION errors which say nothing about capability.

        Defaults to True. An empty capability set means the probe has not run
        or did not succeed, and a transient failure must never silently delete
        a user's entities.

        Returns:
            True unless the vehicle has positively reported it is not capable.
        """
        code = DATAPOINT_ENTITY_KEY_MAP[EntityDescriptionKey(sensor_key)].code

        if code is None or code not in self.incapable_codes:
            return True

        if verbose:
            _LOGGER.debug(
                "Skipping `%s`: %s reports the vehicle is not capable of `%s`",
                sensor_key,
                self.name,
                code,
            )

        return False

    async def async_load_capabilities(self) -> bool:
        """Ask the vehicle which signals it can answer, before entities exist.

        Platforms are set up before the first refresh so the entity registry can
        drive what gets polled, which means capability has to be known earlier
        than the coordinator's own first update. The v3 signals response answers
        both questions at once, so this reads it and keeps the result as
        coordinator data; the caller then skips the first refresh rather than
        making the same request twice.

        Failures are swallowed deliberately. Not knowing the capabilities means
        creating every entity the scopes allow, which is the behaviour before
        this existed, and the skipped first refresh then happens as usual.

        Returns:
            True when the data was loaded and stands in for a first refresh.
        """
        if self.auth.version != "v3":
            return False

        # "disable polling" is the user saying not to make requests to this
        # service on our own initiative. a capability read is exactly that, so
        # it is skipped, and every entity the scopes allow gets created.
        if self.entry.pref_disable_polling:
            _LOGGER.debug(
                "Coordinator %s: polling is disabled, skipping the capability "
                "read; entities are created from the granted scopes alone",
                self.name,
            )
            return False

        # a restart is not a reason to ask the car anything. the response
        # from a few minutes ago describes the same vehicle, and this request
        # is billed: without this, every restart, reload and reconfigure spent
        # a call out of five hundred a month.
        if (
            self.cache is not None
            and (cached := self.cache.fresh(self.vehicle_id)) is not None
        ):
            _LOGGER.debug(
                "Coordinator %s: using the stored signal response rather than "
                "spending a call on setup",
                self.name,
            )
            self.incapable_codes = _incapable_codes(cached)
            self.async_set_updated_data(self._merge_signal_data(cached))

            return True

        try:
            response = await util.async_request_with_retry(
                lambda: self.auth.request_v3(
                    "get", f"vehicles/{self.vehicle_id}/signals"
                ),
                logger=_LOGGER,
                context=f"Capabilities for {self.name}",
            )
            response.raise_for_status()
            signal_data = await response.json()
        except (ClientResponseError, ClientError, ValueError):
            _LOGGER.warning(
                "Coordinator %s: could not read vehicle capabilities; every "
                "entity allowed by the granted scopes will be created",
                self.name,
            )
            return False

        await self.async_record_call()

        if self.cache is not None:
            await self.cache.async_store(self.vehicle_id, signal_data)

        self.incapable_codes = _incapable_codes(signal_data)

        _LOGGER.info(
            "Coordinator %s: vehicle cannot answer %s of %s signals",
            self.name,
            len(self.incapable_codes),
            len(signal_data.get("data", [])),
        )
        _LOGGER.debug(
            "Coordinator %s: unsupported codes: %s",
            self.name,
            sorted(self.incapable_codes),
        )

        # the response is a complete update, so keep it rather than throwing it
        # away and asking again moments later.
        self.async_set_updated_data(self._merge_signal_data(signal_data))

        return True

    def batch_sensor(self, sensor: CoordinatorEntity[Any]) -> None:
        """Mark a sensor to be included in the next update batch."""
        self._batch_add(sensor.entity_description.key)

    def _batch_add(self, key: EntityDescriptionKey | str) -> None:
        """Mark data as needing to be fetched in the next update batch."""
        key = EntityDescriptionKey(key)

        assert self.is_scope_enabled(key)

        self.batch_requests.add(key)

    def _batch_add_defaults(self) -> None:
        """Add default batch paths to request when none were explicitly requested.

        Explicit requests are considered to have been made when:

        - There are already requests that have been made (via the
          `home_assistant.update_entity` action). This method will
          short-circuit and not add additional items to the batch.
        - When polling is disabled, no defaults are added to the batch. This
          prevents requests being made across all endpoints that apply to
          (enabled) entities when Home Assistant starts or the config entry is
          reloaded.

        When polling is enabled and there have been no explicit update
        requests, requests will be added to the batch for all entities that are
        active (not marked as disabled) in the entity registry. (Note: this
        means that during config entry setup, platform initialization needs to
        occur before the first refresh or the entity registry will be empty.)
        """
        if self.batch_requests:
            return
        if (
            self.entry.pref_disable_polling
            or CONF_APPLICATION_MANAGEMENT_TOKEN in self.entry.data
        ):
            return

        entities: list[er.RegistryEntry] = er.async_entries_for_config_entry(
            er.async_get(self.hass), self.entry.entry_id
        )

        for entity in entities:
            _, key = entity.unique_id.split("_", 1)
            if key not in DATAPOINT_ENTITY_KEY_MAP:
                continue
            config = DATAPOINT_ENTITY_KEY_MAP[EntityDescriptionKey(key)]

            if entity.disabled:
                continue

            # v2 can only poll a datapoint that has a v2 endpoint. v3 reads
            # every signal in one request, so any enabled entity is reason
            # enough to refresh, including the ones with no v2 equivalent.
            if self.auth.version == "v2" and not config.endpoint_v2:
                continue

            self._batch_add(key)

    def _batch_process(self) -> list[EntityDescriptionKey]:
        """Process a batch of paths to request.

        Returns:
            The list of entity description keys that need to be processed.
        """
        self._batch_add_defaults()

        result: list[EntityDescriptionKey] = list(self.batch_requests)

        self.batch_requests.clear()

        return result

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API using selective batch endpoint.

        Returns:
            The updated data.

        Raises:
            ConfigEntryAuthFailed: If an authentication failure occurs.
            ClientResponseError: If the update fails for any reason.
            UpdateFailed: If the update fails to provide the proper response.
        """

        batch_requests = self._batch_process()

        # the batch machinery is v2 only: v3 reads every signal in one request
        # and cannot select a subset, so the requested keys decide only whether
        # to refresh, not what to ask for. many v3 signals have no v2 endpoint
        # at all, so asserting on endpoint_v2 for a v3 request would break the
        # update for the whole vehicle the first time someone called
        # homeassistant.update_entity on one of them.
        request_path = f"vehicles/{self.vehicle_id}/batch"
        request_batch_paths: list[str] = []
        request_body: dict[str, Any] = {}

        if self.auth.version == "v2":
            assert not any(
                DATAPOINT_ENTITY_KEY_MAP[key].endpoint_v2 is None
                for key in batch_requests
            )

            request_batch_paths = sorted(
                {
                    v2_endpoint
                    for key in batch_requests
                    if (v2_endpoint := DATAPOINT_ENTITY_KEY_MAP[key].endpoint_v2)
                    is not None
                }
            )
            request_body = {
                "requests": [{"path": path} for path in request_batch_paths]
            }

        if not batch_requests:
            _LOGGER.warning(
                "Coordinator %s: No updates to request based on granted scopes and context.",
                self.name,
            )
            return self.data

        # the allowance is per vehicle per month and is shared with commands.
        # stopping short of the ceiling keeps the reserve available for a lock
        # or a charge stop, which matter more than one more reading.
        if not self.budget_allows_poll():
            _LOGGER.warning(
                "Coordinator %s: skipping scheduled poll, the vehicle has used "
                "%s of its %s calls this period and %s are reserved for "
                "commands. Subscribe the vehicle to a webhook for updates that "
                "do not spend the allowance",
                self.name,
                self.budget.used(self.vehicle_id) if self.budget else "?",
                self.monthly_budget,
                self.budget_reserve,
            )
            return self.data

        _LOGGER.debug(
            "Coordinator %s: Requesting batch update (Interval: %s) for paths: %s",
            self.name,
            self.update_interval,
            request_batch_paths,
        )

        try:
            if self.auth.version == "v2":
                response = await util.async_request_with_retry(
                    lambda: self.auth.request_v2(
                        "post", request_path, json=request_body
                    ),
                    logger=_LOGGER,
                    context=f"Coordinator {self.name}",
                )
            else:
                assert self.auth.version == "v3"
                request_path = f"vehicles/{self.vehicle_id}/signals"
                response = await util.async_request_with_retry(
                    lambda: self.auth.request_v3("get", request_path),
                    logger=_LOGGER,
                    context=f"Coordinator {self.name}",
                )

        # response errors here for responses that have actually completed, i.e.
        # 4xx responses are for errors related to requests made in the
        # underlying oauth handler. for instance, the implementation will raise
        # for invalid an invalid status while negotiating a new token if there's
        # an issue. unfortunately, it consumes the JSON response to log about
        # the error, so we can only match on the status code.
        except ClientResponseError as exception:
            if exception.status in {
                HTTPStatus.BAD_REQUEST,
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            }:
                raise ConfigEntryAuthFailed from exception
            raise

        if response.status in {
            HTTPStatus.TOO_MANY_REQUESTS,
            HTTPStatus.INTERNAL_SERVER_ERROR,
        }:
            msg = f"API returned {response.status} after retries"
            raise UpdateFailed(msg)

        response.raise_for_status()
        response_data = await response.json()

        await self.async_record_call()

        return self._merge_response_data(response_data)

    def _merge_response_data(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Merge an update response according to the API version in use.

        Returns:
            The newly merged data.

        Raises:
            UpdateFailed: If a v2 batch response has no responses array.
        """
        if self.auth.version == "v2":
            if "responses" not in response_data:
                msg = "Invalid batch response format"
                raise UpdateFailed(msg)

            return self._merge_batch_data(response_data)

        assert self.auth.version == "v3"

        return self._merge_signal_data(response_data)

    def _merge_batch_data(self, batch_data: dict[str, Any]) -> dict[str, Any]:
        """Merge data from the responses from a batch request.

        Returns:
            The newly merged data.
        """

        with self.create_updated_data() as (add, updated_data):
            for item in batch_data["responses"]:
                path = item["path"]
                code = item["code"]
                body = item["body"]
                headers = item.get("headers") or {}
                unit_system = headers.get("sc-unit-system")
                data_age = headers.get("sc-data-age")
                fetched_at = headers.get("sc-fetched-at")
                key = path.strip("/").replace("/", "_")

                if code != 200:
                    body = None
                    unit_system = None
                    data_age = None
                    fetched_at = None

                if data_age:
                    data_age = dt_util.parse_datetime(data_age)
                if fetched_at:
                    fetched_at = dt_util.parse_datetime(fetched_at)

                add.from_response_body_v2(
                    key,
                    body=body,
                    unit_system=unit_system,
                    data_age=data_age,
                    fetched_at=fetched_at,
                )

                if code not in {200, 404}:
                    _LOGGER.warning(
                        "Coordinator %s: Status %s for path %s",
                        self.name,
                        code,
                        path,
                    )

            _LOGGER.debug("Coordinator %s: Batch update processed", self.name)

            return updated_data

    def note_live_webhook(self) -> None:
        """Log once when the first non-TEST webhook delivery arrives.

        `mode=TEST` deliveries (a dashboard "Send test event") prove the path
        works but say nothing about the real subscription; this is the line
        that confirms Smartcar is actually pushing this vehicle's data.
        """
        if self._live_webhook_seen:
            return

        self._live_webhook_seen = True
        _LOGGER.info("Coordinator %s: first live webhook delivery received", self.name)

    def _empty_store_issue_id(self) -> str:
        return f"empty_signal_store_{self.entry.entry_id}_{self.vehicle_id}"

    def clear_empty_store_issue(self) -> None:
        """Clear the "no signals collected yet" issue for this vehicle.

        Called the moment any data is seen, whether from a poll or a webhook
        delivery, so the issue never outlives the problem it describes.
        """
        ir.async_delete_issue(self.hass, DOMAIN, self._empty_store_issue_id())

    def _update_empty_store_issue(self, *, has_signals: bool) -> None:
        """Track whether this vehicle's Smartcar signal store has ever filled.

        Only a poll of the whole store (`GET /vehicles/{id}/signals`) is
        trustworthy evidence of emptiness: a single webhook delivery with no
        signals just means nothing changed, not that the store is empty. So
        this is only ever called with the result of a poll.
        """
        if has_signals:
            was_empty = (
                ir.async_get(self.hass).async_get_issue(
                    DOMAIN, self._empty_store_issue_id()
                )
                is not None
            )
            self.clear_empty_store_issue()

            if was_empty:
                _LOGGER.info(
                    "Coordinator %s: signal store is no longer empty", self.name
                )

            return

        details = self.entry.data.get("vehicles", {}).get(self.vehicle_id, {})
        make = details.get("make")
        model = details.get("model")
        vehicle_name = f"{make} {model}" if make and model else self.vehicle_id

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._empty_store_issue_id(),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="empty_signal_store",
            translation_placeholders={
                "title": self.entry.title,
                "vehicle_name": vehicle_name,
            },
        )

    def _log_poll_summary(
        self, signals: list[dict[str, Any]], total: int | None
    ) -> None:
        """Log what a `GET /vehicles/{id}/signals` poll actually contained.

        The point of this line is answering "why is my entity still unknown"
        without a packet capture: which codes came back, which of those map
        to an entity at all, and which came back with an error status rather
        than a value.
        """
        mapped_count = 0
        unmapped_codes: list[str] = []
        error_codes: list[str] = []

        for signal in signals:
            attributes = signal.get("attributes", {})
            code = attributes.get("code")

            if not code:
                continue

            if code in DATAPOINT_CODE_MAP:
                mapped_count += 1
            else:
                unmapped_codes.append(code)

            if attributes.get("status", {}).get("value") == "ERROR":
                error_codes.append(code)

        self.last_poll_total_count = total
        self.last_poll_unmapped_codes = unmapped_codes

        _LOGGER.debug(
            "Coordinator %s: poll totalCount=%s returned=%s mapped=%s "
            "unmapped=%s errored=%s",
            self.name,
            total,
            len(signals),
            mapped_count,
            unmapped_codes,
            error_codes,
        )

    def _merge_signal_data(self, signal_data: dict[str, Any]) -> dict[str, Any]:
        """Merge data response data from a v3 vehicle signals request.

        Returns:
            The newly merged data.
        """
        signals = signal_data.get("data", [])
        total = signal_data.get("meta", {}).get("totalCount")

        self._update_empty_store_issue(has_signals=bool(signals))
        self._log_poll_summary(signals, total)

        # the signals response is JSON:API shaped and advertises paging, but no
        # page parameter is documented on this endpoint. in every capture the
        # page has held the whole set. say so loudly if that ever stops being
        # true, rather than quietly dropping signals.
        if total is not None and total > len(signals):
            _LOGGER.warning(
                "Coordinator %s: signals response reported %s signals but "
                "returned %s; some entities will not update",
                self.name,
                total,
                len(signals),
            )

        with self.create_updated_data() as (add, updated_data):
            for signal in signals:
                attributes = signal.get("attributes", {})
                add.from_signal_attributes(
                    {
                        **attributes,
                        "meta": {
                            **attributes.get("meta", {}),
                            **signal.get("meta", {}),
                        },
                    }
                )

            _LOGGER.debug("Coordinator %s: Signal polling update processed", self.name)

            return updated_data

    @contextmanager
    def create_updated_data(
        self,
    ) -> Generator[tuple[_DataAdder, dict[str, Any]]]:
        updated_data = dict(self.data or {})

        yield _DataAdder(updated_data), updated_data


def _incapable_codes(signal_data: dict[str, Any]) -> frozenset[str]:
    """Signal codes the vehicle reported it is structurally incapable of.

    Only COMPATIBILITY counts. A VEHICLE_STATE error means "not right now"
    (charge rate while unplugged), and a PERMISSION error means the user needs
    to re-consent. Neither is a statement about the vehicle's capability, and
    treating either as one would delete entities that work.

    Returns:
        The set of codes to treat as unsupported.
    """
    return frozenset(
        code
        for signal in signal_data.get("data", [])
        if (attributes := signal.get("attributes", {}))
        and (code := attributes.get("code"))
        and (status := attributes.get("status", {})).get("value") == "ERROR"
        and status.get("error", {}).get("type") == "COMPATIBILITY"
    )


class _DataAdder:
    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__()
        self.data = data
        self._addition_made = False

    @property
    def addition_made(self) -> bool:
        return self._addition_made

    def from_signal_attributes(self, signal: dict) -> None:
        name: str | None = signal.get("name")
        status = signal.get("status", {})
        is_error = status.get("value") == "ERROR"
        code: str | None = signal.get("code")
        body = copy.deepcopy(signal.get("body", {}))
        meta = signal.get("meta", {})

        if is_error:
            _handle_webhook_signal_error(
                name,
                status.get("error", {}),
                level="error" if _is_integrated(signal) else "debug",
            )

            body = {"value": None}

        _normalize_body_values_key(code, body)

        if body.get("unit") == "percent":
            _handle_percent_unit_conversion(code, body)

        if code in DATAPOINT_CODE_MAP:
            assert code is not None

            data_age = meta.get("oemUpdatedAt") if not is_error else None
            fetched_at = meta.get("retrievedAt") if not is_error else None
            unit = body.pop("unit", None)
            unit_system = (
                "imperial"
                if unit in _IMPERIAL_MEASUREMENTS
                else "metric"
                if unit
                else None
            )

            data_age = _parse_signal_timestamp(data_age)
            fetched_at = _parse_signal_timestamp(fetched_at)

            self.from_response_body(
                code,
                body=body,
                unit_system=unit_system,
                data_age=data_age,
                fetched_at=fetched_at,
                can_clear_meta=not is_error,
            )

    def from_response_body(
        self,
        code: str,
        *,
        body: dict[str, Any] | None,
        data_age: dt.datetime | None = None,
        fetched_at: dt.datetime | None = None,
        unit_system: str | None = None,
        can_clear_meta: bool = True,
    ) -> None:
        for datapoint in DATAPOINT_CODE_MAP[code]:
            assert code == datapoint.code

            self.data[datapoint.storage_key] = (
                ((self.data.get(datapoint.storage_key) or {}) | body)
                if body is not None
                else None
            )

        self._update_meta(
            DATAPOINT_CODE_MAP[code],
            data_age=data_age,
            fetched_at=fetched_at,
            unit_system=unit_system,
            can_clear=can_clear_meta,
        )

    def from_response_body_v2(
        self,
        storage_key_v2: str,
        *,
        body: dict[str, Any] | None,
        data_age: dt.datetime | None = None,
        fetched_at: dt.datetime | None = None,
        unit_system: str | None = None,
        can_clear_meta: bool = True,
    ) -> None:
        for datapoint in DATAPOINT_STORAGE_KEY_V2_MAP[storage_key_v2]:
            assert storage_key_v2 == datapoint.storage_key_v2

            value = (
                None
                if body is None
                else key_path_get(body, datapoint.value_key_path_v2)
                if datapoint.value_key_path_v2
                else body
            )

            self.data[datapoint.storage_key] = datapoint.value_merge_v2(
                self.data.get(datapoint.storage_key) or {},
                datapoint.value_transform_v2(value),
            )

        self._update_meta(
            DATAPOINT_STORAGE_KEY_V2_MAP[storage_key_v2],
            data_age=data_age,
            fetched_at=fetched_at,
            unit_system=unit_system,
            can_clear=can_clear_meta,
        )

    def from_storage_raw_value(
        self,
        entity_description_key: EntityDescriptionKey,
        value_key_path: str,
        *,
        value: Any,  # noqa: ANN401
        data_age: dt.datetime | None = None,
        fetched_at: dt.datetime | None = None,
        unit_system: str | None = None,
        can_clear_meta: bool = True,
    ) -> None:
        datapoint = DATAPOINT_ENTITY_KEY_MAP[entity_description_key]

        # when needed, transform stored v2 raw values by using the
        # `value_transform_v2`, but since this changes a value into
        # an object that looks like the v3 body, i.e. it is nested
        # inside a dict with a `value(s)` key, we need to strip off
        # the final key from the key path (and ensure it matches
        # something within the new value as a sanity check).
        if datapoint.is_v2_value(value):
            value = datapoint.value_transform_v2(value)
            storage_key, final_key = value_key_path.rsplit(".", 1)
            assert final_key in value
            _LOGGER.debug("merge %s into %s", value, self.data.get(storage_key))
            self.data[storage_key] = datapoint.value_merge_v2(
                self.data.get(storage_key) or {},
                value,
            )
        else:
            key_path_update(self.data, value_key_path, value)

        self._update_meta(
            (datapoint,),
            data_age=data_age,
            fetched_at=fetched_at,
            unit_system=unit_system,
            can_clear=can_clear_meta,
        )

    def _update_meta(
        self,
        datapoints: tuple[DatapointConfig, ...],
        *,
        data_age: dt.datetime | None,
        fetched_at: dt.datetime | None,
        unit_system: str | None,
        can_clear: bool,
    ) -> None:
        self._addition_made = True

        for datapoint in datapoints:
            storage_key = datapoint.storage_key

            if unit_system:
                self.data[f"{storage_key}:unit_system"] = unit_system
            elif can_clear:
                self.data.pop(f"{storage_key}:unit_system", None)

            if data_age:
                self.data[f"{storage_key}:data_age"] = data_age
            elif can_clear:
                self.data.pop(f"{storage_key}:data_age", None)

            if fetched_at:
                self.data[f"{storage_key}:fetched_at"] = fetched_at
            elif can_clear:
                self.data.pop(f"{storage_key}:fetched_at", None)


def _parse_signal_timestamp(value: str | float | None) -> dt.datetime | None:
    """Parse ISO timestamps from polling or epoch milliseconds from webhooks.

    Returns:
        The signal timestamp, or None when unavailable or an invalid ISO string.
    """
    timestamp: dt.datetime | None = None
    if isinstance(value, str):
        timestamp = dt_util.parse_datetime(value)
    elif value:
        timestamp = dt_util.utc_from_timestamp(value / 1000)
    return timestamp


def _is_integrated(signal: dict) -> bool:
    code: str | None = signal.get("code")
    return code in DATAPOINT_CODE_MAP


# the OpenAPI spec's own example bodies for these two codes use a signal-name
# keyed array (`doors`, `windows`) where every other multi-item signal, and
# every live payload actually observed, uses the generic `values` key that
# entity descriptions (`closure-doors.values`, `closure-windows.values`) read.
# accept either so a body shaped like the spec's example does not leave the
# entity stuck unknown.
_SIGNAL_BODY_ALTERNATE_VALUES_KEY = {
    "closure-doors": "doors",
    "closure-windows": "windows",
}


def _normalize_body_values_key(code: str | None, body: dict[str, Any]) -> None:
    if (
        "values" not in body
        and (alternate := _SIGNAL_BODY_ALTERNATE_VALUES_KEY.get(code or ""))
        and alternate in body
    ):
        body["values"] = body.pop(alternate)


def _handle_percent_unit_conversion(code: str | None, body: dict[str, Any]) -> None:
    if "values" in body:
        item_key = _SIGNAL_BODY_MULTIVALUE_ITEM_KEY_MAP.get(code) or "value"
        values = body["values"]
        values = [value | {item_key: value[item_key] / 100} for value in values]
        body["values"] = values
        body.pop("unit")
    else:
        body["value"] /= 100
        body.pop("unit")


def _handle_webhook_signal_error(
    signal_name: str | None,
    error: dict,
    *,
    level: Literal["error", "debug"] = "error",
) -> None:
    error_type = error.get("type")
    error_code = error.get("code")

    if (error_type, error_code) in _BENIGN_SIGNAL_ERRORS:
        level = "debug"

    logger_method = getattr(_LOGGER, level)
    logger_method("error for signal %s: %s:%s", signal_name, error_type, error_code)
