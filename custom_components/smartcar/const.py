from enum import StrEnum, auto

from .types import APIVersion

DOMAIN = "smartcar"
DEFAULT_NAME = "Smartcar"
API_ENDPOINT = "https://vehicle.api.smartcar.com/v3"
API_ENDPOINT_LEGACY = "https://api.smartcar.com/v2.0"
API_ENDPOINTS: dict[APIVersion, str] = {"v2": API_ENDPOINT_LEGACY, "v3": API_ENDPOINT}


PLATFORMS = [
    "sensor",
    "switch",
    "lock",
    "device_tracker",
    "binary_sensor",
    "number",
]

OAUTH2_AUTHORIZE = "https://connect.smartcar.com/oauth/authorize"
OAUTH2_TOKEN = "https://iam.smartcar.com/oauth2/token"  # noqa: S105
OAUTH2_TOKEN_LEGACY = "https://auth.smartcar.com/oauth/token"  # noqa: S105
SMARTCAR_MODE = "live"

CONF_APPLICATION_ID = "application_id"
CONF_APPLICATION_MANAGEMENT_TOKEN = "application_management_token"  # noqa: S105
CONF_CLOUDHOOK = "cloudhook"

# polling policy. see polling.py for why these exist: the free tier allows 500
# calls per vehicle per month, and an unsubscribed vehicle's data only refreshes
# about once a day, so polling hard is both expensive and pointless.
CONF_POLL_PROFILE = "poll_profile"
CONF_POLL_INTERVAL_HOURS = "poll_interval_hours"
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_POLL_ON_LEAVE_HOME = "poll_on_leave_home"
CONF_POLL_ON_ARRIVE_HOME = "poll_on_arrive_home"
CONF_LOW_BATTERY = "low_battery_threshold"
CONF_DAILY_EVENT_CAP = "daily_event_cap"
CONF_MONTHLY_BUDGET = "monthly_budget"
CONF_BUDGET_RESERVE = "budget_reserve"
CONF_AUTO_SUBSCRIBE = "auto_subscribe_webhook"


class Scope(StrEnum):
    """Scope enumeration class."""

    READ_VEHICLE_INFO = auto()
    READ_VIN = auto()
    READ_BATTERY = auto()
    READ_CHARGE = auto()
    READ_ENGINE_OIL = auto()
    READ_FUEL = auto()
    READ_LOCATION = auto()
    READ_ODOMETER = auto()
    READ_SECURITY = auto()
    READ_TIRES = auto()
    CONTROL_CHARGE = auto()
    CONTROL_SECURITY = auto()
    READ_DIAGNOSTICS = auto()
    READ_CLIMATE = auto()
    CONTROL_CLIMATE = auto()


REQUIRED_SCOPES = [
    Scope.READ_VEHICLE_INFO,
    Scope.READ_VIN,
]

# Every permission this integration knows how to use. v3 Connect is sent no
# `scope` at all, because a scope in the Connect URL overrides the dashboard's
# Vehicle Access configuration, which is the one place the user can see and
# change what they are granting. v2 Connect has no dashboard equivalent, so it
# asks for everything and lets Smartcar's consent screen decide.
ALL_SCOPES = list(Scope)


class EntityDescriptionKey(StrEnum):
    """EntityDescriptionKey enumeration class."""

    PLUG_STATUS = auto()
    LOCATION = auto()
    DOOR_LOCK = auto()
    DOOR_BACK_LEFT = auto()
    DOOR_BACK_RIGHT = auto()
    DOOR_FRONT_LEFT = auto()
    DOOR_FRONT_RIGHT = auto()
    DOOR_BACK_LEFT_LOCK = auto()
    DOOR_BACK_RIGHT_LOCK = auto()
    DOOR_FRONT_LEFT_LOCK = auto()
    DOOR_FRONT_RIGHT_LOCK = auto()
    CHARGE_LIMIT = auto()
    CHARGE_CHARGERATE = auto()
    CHARGE_ENERGYADDED = auto()
    CHARGE_TIMETOCOMPLETE = auto()
    CHARGING = auto()
    BATTERY_CAPACITY = auto()
    BATTERY_LEVEL = auto()
    BATTERY_HEATER_ACTIVE = auto()
    CHARGING_STATE = auto()
    ENGINE_OIL = auto()
    ENGINE_COVER = auto()
    FUEL = auto()
    FUEL_PERCENT = auto()
    FUEL_RANGE = auto()
    GEAR_STATE = auto()
    LOW_VOLTAGE_BATTERY_LEVEL = auto()
    ODOMETER = auto()
    RANGE = auto()
    TIRE_PRESSURE_BACK_LEFT = auto()
    TIRE_PRESSURE_BACK_RIGHT = auto()
    TIRE_PRESSURE_FRONT_LEFT = auto()
    TIRE_PRESSURE_FRONT_RIGHT = auto()
    WINDOW_BACK_LEFT = auto()
    WINDOW_BACK_RIGHT = auto()
    WINDOW_FRONT_LEFT = auto()
    WINDOW_FRONT_RIGHT = auto()
    FRONT_TRUNK = auto()
    FRONT_TRUNK_LOCK = auto()
    REAR_TRUNK = auto()
    REAR_TRUNK_LOCK = auto()
    SUNROOF = auto()
    ONLINE = auto()
    ASLEEP = auto()
    DIGITAL_KEY_PAIRED = auto()
    SURVEILLANCE_ENABLED = auto()
    CHARGE_VOLTAGE = auto()
    CHARGE_AMPERAGE = auto()
    CHARGE_WATTAGE = auto()
    CHARGE_TIME_TO_COMPLETE = auto()
    CHARGE_AMPERAGE_MAX = auto()
    CHARGE_FAST_CHARGER_PRESENT = auto()
    FIRMWARE_VERSION = auto()
    LAST_WEBHOOK_RECEIVED = auto()
    API_CALLS_USED = auto()
    API_CALLS_REMAINING = auto()
    NEXT_SCHEDULED_POLL = auto()
    # Diagnostics (requires read_diagnostics)
    DIAG_ABS = auto()
    DIAG_MIL = auto()
    DIAG_DTC_COUNT = auto()
    DIAG_DTC_LIST = auto()
    DIAG_EV_BATTERY_CONDITIONING = auto()
    DIAG_EV_CHARGING = auto()
    DIAG_EV_DRIVE_UNIT = auto()
    DIAG_EV_HV_BATTERY = auto()
    # Climate status (requires read_climate)
    CABIN_TARGET_TEMPERATURE = auto()
    IS_CABIN_HVAC_ACTIVE = auto()
    IS_FRONT_DEFROSTER_ACTIVE = auto()
    IS_REAR_DEFROSTER_ACTIVE = auto()
    IS_STEERING_HEATER_ACTIVE = auto()
    # Climate control (requires read_climate + control_climate)
    CLIMATE = auto()


DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS = {
    EntityDescriptionKey.BATTERY_LEVEL,
    EntityDescriptionKey.CHARGING_STATE,
    EntityDescriptionKey.CHARGING,
    EntityDescriptionKey.DOOR_LOCK,
    EntityDescriptionKey.LOCATION,
    EntityDescriptionKey.PLUG_STATUS,
    EntityDescriptionKey.RANGE,
    EntityDescriptionKey.DIAG_ABS,
    EntityDescriptionKey.DIAG_MIL,
    EntityDescriptionKey.DIAG_DTC_COUNT,
    EntityDescriptionKey.DIAG_DTC_LIST,
    EntityDescriptionKey.DIAG_EV_BATTERY_CONDITIONING,
    EntityDescriptionKey.DIAG_EV_CHARGING,
    EntityDescriptionKey.DIAG_EV_DRIVE_UNIT,
    EntityDescriptionKey.DIAG_EV_HV_BATTERY,
    EntityDescriptionKey.CABIN_TARGET_TEMPERATURE,
    EntityDescriptionKey.IS_CABIN_HVAC_ACTIVE,
    EntityDescriptionKey.IS_FRONT_DEFROSTER_ACTIVE,
    EntityDescriptionKey.IS_REAR_DEFROSTER_ACTIVE,
    EntityDescriptionKey.IS_STEERING_HEATER_ACTIVE,
    EntityDescriptionKey.CLIMATE,
}
