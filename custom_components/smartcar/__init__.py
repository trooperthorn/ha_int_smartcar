import asyncio
from functools import partial
from http import HTTPStatus
import logging
from typing import cast

from aiohttp import ClientError, ClientResponseError
from homeassistant.components import cloud, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, CONF_WEBHOOK_ID
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2Implementation,
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import events, util
from .auth import AbstractAuth
from .auth_impl import AccessTokenAuthImpl, AsyncConfigEntryAuth
from .budget import ApiBudget
from .cache import SignalCache
from .const import (
    ALL_SCOPES,
    API_ENDPOINTS,
    CONF_AUTO_SUBSCRIBE,
    CONF_CLOUDHOOK,
    CONF_POLL_INTERVAL_HOURS,
    CONF_POLL_PROFILE,
    DOMAIN,
    PLATFORMS,
    REQUIRED_SCOPES,
)
from .coordinator import SmartcarVehicleCoordinator
from .errors import (
    EmptyVehicleListError,
    InvalidAuthError,
    MissingRequiredPermissionsError,
    UnsupportedUserConfigurationError,
)
from .management import ManagementApi, webhook_health, webhook_id_matching_url
from .polling import PollProfile, presence_transition_wants_poll, resolve_settings
from .services import async_setup_services
from .types import APIVersion, SmartcarConfigEntry, SmartcarData
from .util import api_version_for_client_id
from .webhooks import handle_webhook, webhook_url_from_id

_LOGGER = logging.getLogger(__name__)


async def async_setup(  # noqa: RUF029
    hass: HomeAssistant,
    config: ConfigType,  # noqa: ARG001
) -> bool:
    """Set up Smartcar services.

    Returns:
        If the setup was successful.
    """
    async_setup_services(hass)

    return True


def _register_vehicles(
    hass: HomeAssistant,
    entry: SmartcarConfigEntry,
    *,
    auth: AbstractAuth,
    version: APIVersion,
    budget: ApiBudget,
    cache: SignalCache,
    coordinators: dict[str, SmartcarVehicleCoordinator],
) -> None:
    """Register a device and build a coordinator for each configured vehicle.

    Raises:
        ConfigEntryError: For a VIN already used by another config entry.
    """
    device_registry = dr.async_get(hass)
    other_vins = vehicle_vins_in_use(hass, entry)

    for vehicle_id, details in entry.data.get("vehicles", {}).items():
        vin = details.get("vin")
        make = details.get("make")
        model = details.get("model")
        year = details.get("year")

        if vin is not None and vin in other_vins:
            msg = f"Cannot setup multiple config entries with VIN {vin}"
            raise ConfigEntryError(msg)

        device_id = vehicle_id

        if version == "v2" and vin:
            device_id = vin

        # register device
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device_id)},
            manufacturer=make,
            model=f"{model} ({year})" if model and year else model,
            name=f"{make} {model}"
            if make and model
            else f"Smartcar {(vin or vehicle_id)[-4:]}",
        )
        _LOGGER.info("Registered device for %s (VIN: %s)", vehicle_id, vin)

        # create and store coordinator
        coordinators[vehicle_id] = SmartcarVehicleCoordinator(
            hass,
            auth=auth,
            vehicle_id=vehicle_id,
            vin=vin,
            entry=entry,
            version=version,
            budget=budget,
            cache=cache,
        )
        _LOGGER.debug(
            "Coordinator created and initial data fetched for %s (VIN: %s)",
            vehicle_id,
            vin,
        )


async def async_setup_entry(hass: HomeAssistant, entry: SmartcarConfigEntry) -> bool:
    """Set up Smartcar from a config entry.

    Returns:
        If the setup was successful.
    """
    implementation = cast(
        "LocalOAuth2Implementation",
        await async_get_config_entry_implementation(hass, entry),
    )
    version = api_version_for_client_id(implementation.client_id)
    auth = AsyncConfigEntryAuth(
        async_get_clientsession(hass),
        implementation,
        OAuth2Session(hass, entry, implementation),
        API_ENDPOINTS,
        user_id=entry.data.get("user_id"),
    )
    budget = await ApiBudget(hass, entry.entry_id).async_load()
    cache = await SignalCache(hass, entry.entry_id).async_load()
    coordinators: dict[str, SmartcarVehicleCoordinator] = {}
    meta_coordinator = DataUpdateCoordinator(
        hass, _LOGGER, name=f"{DOMAIN}_meta", config_entry=entry
    )
    meta_coordinator.async_set_updated_data({})
    entry.runtime_data = SmartcarData(
        auth=auth,
        coordinators=coordinators,
        meta_coordinator=meta_coordinator,
        budget=budget,
        management=ManagementApi(auth, async_get_clientsession(hass)),
    )
    _register_vehicles(
        hass,
        entry,
        auth=auth,
        version=version,
        budget=budget,
        cache=cache,
        coordinators=coordinators,
    )

    # capabilities have to be known before the platforms run, because the
    # platforms decide which entities to create and a vehicle that cannot answer
    # a signal should not get an entity for it. the v3 signals response answers
    # capability and carries the current values, so its result is kept and the
    # first refresh below is skipped: same number of requests as before.
    refresh_needed = await async_load_capabilities(list(coordinators.values()))

    # setup platforms before doing first refresh. this gets the entity registry
    # populated with the desired entities & allows the coordinator to determine
    # what to fetch on the first refresh. (some entities, for instance, are
    # disabled by default.)
    _LOGGER.debug("Forwarding setup to platforms: %s", PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if CONF_WEBHOOK_ID in entry.data:
        # the URL is not secret, but it is only useful with debug logging on,
        # same as everything else in the webhook troubleshooting path.
        _LOGGER.debug(
            "Registering webhook at url: %s",
            (await webhook_url_from_id(hass, entry.data[CONF_WEBHOOK_ID]))[0],
        )
        webhook.async_register(
            hass,
            DOMAIN,
            entry.title,
            entry.data[CONF_WEBHOOK_ID],
            partial(handle_webhook, config_entry=entry),
        )
    else:
        _LOGGER.debug("Webhooks are not enabled")

    if auth.version == "v2":
        events.create_issue(
            hass,
            issue_id=f"legacy_client_id_{entry.entry_id}",
            entry_id=entry.entry_id,
            is_fixable=True,
            is_persistent=True,
            severity=IssueSeverity.WARNING,
            translation_key="legacy_client_id",
            translation_placeholders={
                "title": entry.title,
                "docs_url": "https://github.com/wbyoung/smartcar#upgrading-from-legacy-v2-api-to-v3",
            },
        )

    await asyncio.gather(
        *[async_do_first_refresh(coordinator) for coordinator in refresh_needed]
    )

    async_setup_presence_trigger(hass, entry)

    if entry.options.get(CONF_AUTO_SUBSCRIBE) and CONF_WEBHOOK_ID in entry.data:
        entry.async_create_background_task(
            hass,
            async_subscribe_vehicles(hass, entry),
            name=f"{DOMAIN}_subscribe_{entry.entry_id}",
        )

    # log stored scopes once on successful setup
    _LOGGER.info(
        "Using token with scopes: %s", entry.data.get("token", {}).get("scopes")
    )

    return True


@callback
def async_setup_presence_trigger(
    hass: HomeAssistant, entry: SmartcarConfigEntry
) -> None:
    """Poll when a watched person or tracker crosses the home boundary.

    Where the people are is something Home Assistant already knows, for free
    and as often as it likes. Where the car is costs one of a few hundred calls
    a month. So the phone is the trigger and the car is the question, rather
    than polling the car to discover something the phone already said.
    """
    settings = resolve_settings(dict(entry.options))

    if not settings.watches_presence:
        return

    async def _handle(event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if not presence_transition_wants_poll(
            settings,
            old_state.state if old_state else None,
            new_state.state if new_state else None,
        ):
            return

        reason = f"{event.data['entity_id']} is now {new_state.state if new_state else 'unknown'}"

        for coordinator in entry.runtime_data.coordinators.values():
            await coordinator.async_request_event_poll(reason)

    entry.async_on_unload(
        async_track_state_change_event(hass, list(settings.presence_entities), _handle)
    )

    _LOGGER.debug(
        "Watching %s for presence changes that should refresh the vehicles",
        ", ".join(settings.presence_entities),
    )


async def async_subscribe_vehicles(
    hass: HomeAssistant, entry: SmartcarConfigEntry
) -> None:
    """Subscribe every vehicle to the webhook that points at this instance.

    A subscribed vehicle is pushed data as often as the OEM allows and costs
    nothing from its monthly allowance, which is the only way to have both
    fresh data and a working budget. Doing it here removes the manual dashboard
    step that was previously required per vehicle.
    """
    runtime = entry.runtime_data
    management = runtime.management
    user_id = entry.data.get("user_id")

    if not user_id:
        _LOGGER.debug("No user id stored; cannot subscribe vehicles")
        return

    if not (webhook_id := entry.data.get(CONF_WEBHOOK_ID)):
        _LOGGER.debug("No webhook configured; nothing to subscribe vehicles to")
        return

    callback_url, _cloudhook = await webhook_url_from_id(hass, webhook_id)
    webhooks = await management.async_list_webhooks()
    seen_uris = _seen_callback_uris(webhooks)
    # the cloudhook/webhook URL is not secret, but it is only useful with
    # debug logging on, same as everything else in this troubleshooting path.
    _LOGGER.debug(
        "Smartcar webhook match: expected callback URL %s, seen: %s",
        callback_url,
        seen_uris or "(none configured)",
    )
    target_webhook = webhook_id_matching_url(webhooks, callback_url)

    if target_webhook is None:
        _LOGGER.warning(
            "No Smartcar webhook has the callback URL Home Assistant expects "
            "(%s), so vehicles cannot be subscribed automatically. The "
            "webhook(s) on this application currently point at: %s. A "
            "webhook pointed at another URL (for example the Smartcar "
            "Connect redirect) will never be verified by this instance",
            callback_url,
            seen_uris or "(none configured)",
        )
        events.delete_issue(
            hass,
            issue_id=f"webhook_unhealthy_{entry.entry_id}",
            entry_id=entry.entry_id,
            translation_key="webhook_unhealthy",
            severity=IssueSeverity.WARNING,
        )
        events.create_issue(
            hass,
            issue_id=f"no_matching_webhook_{entry.entry_id}",
            entry_id=entry.entry_id,
            is_fixable=False,
            is_persistent=True,
            severity=IssueSeverity.WARNING,
            translation_key="no_matching_webhook",
            translation_placeholders={
                "title": entry.title,
                "callback_url": callback_url,
                "seen_callback_uris": seen_uris or "(none configured)",
            },
        )
        return

    events.delete_issue(
        hass,
        issue_id=f"no_matching_webhook_{entry.entry_id}",
        entry_id=entry.entry_id,
        translation_key="no_matching_webhook",
        severity=IssueSeverity.WARNING,
    )
    # the Management API's webhook resource (management.yaml) exposes name,
    # callbackUri, isEnabled, triggers, data, errorCallbackUri and
    # autoSubscribe, but no verification status field, so a matched-but-
    # unverified webhook cannot be told apart from a matched-and-verified one
    # here. the callback URI mismatch above is the detectable half of that
    # failure mode; a verified-but-otherwise-unhealthy webhook is still
    # covered by the enabled/triggers/data check below.
    _check_webhook_health(hass, entry, webhooks, target_webhook)

    for vehicle_id in runtime.coordinators:
        if await management.async_subscriptions_for_vehicle(vehicle_id):
            _LOGGER.debug("Vehicle %s already has a subscription", vehicle_id)
            continue

        await management.async_subscribe(
            webhook_id=target_webhook, user_id=user_id, vehicle_id=vehicle_id
        )


def _seen_callback_uris(webhooks: list[dict]) -> str:
    """The callback URIs actually configured on the application's webhooks.

    Surfaced so a mismatch (for example the Smartcar Connect redirect saved
    into the webhook's own callback URI field, which verifies nothing) is
    visible without a trip to the dashboard.

    Returns:
        A comma separated list of the distinct callback URIs seen.
    """
    uris = {
        str(uri)
        for webhook in webhooks
        if isinstance(attributes := webhook.get("attributes", {}), dict)
        and (uri := attributes.get("callbackUri") or attributes.get("url"))
    }

    return ", ".join(sorted(uris))


def _check_webhook_health(
    hass: HomeAssistant,
    entry: SmartcarConfigEntry,
    webhooks: list[dict],
    target_webhook: str,
) -> None:
    """Warn once when the webhook pointing here cannot actually collect data.

    A webhook that is disabled, or has no triggers, or no data signals still
    passes the "does a webhook exist for this URL" check above, and every
    entity then stays unknown with nothing in the logs to explain it. This is
    exactly the failure Sean hit: `isEnabled: false` with empty `triggers`
    and `data`.
    """
    issue_id = f"webhook_unhealthy_{entry.entry_id}"
    health = webhook_health(webhooks, target_webhook)

    _LOGGER.debug(
        "Smartcar webhook %s health: enabled=%s, triggers=%s, data=%s",
        target_webhook,
        health.is_enabled if health else None,
        health.trigger_count if health else None,
        health.data_count if health else None,
    )

    if health is None or health.is_healthy:
        events.delete_issue(
            hass,
            issue_id=issue_id,
            entry_id=entry.entry_id,
            translation_key="webhook_unhealthy",
            severity=IssueSeverity.WARNING,
        )
        return

    _LOGGER.warning(
        "Smartcar webhook %s cannot collect data (enabled=%s, triggers=%s, "
        "data=%s); entities will stay unknown until it is fixed in the "
        "Smartcar dashboard",
        health.webhook_id,
        health.is_enabled,
        health.trigger_count,
        health.data_count,
    )

    events.create_issue(
        hass,
        issue_id=issue_id,
        entry_id=entry.entry_id,
        is_fixable=False,
        is_persistent=True,
        severity=IssueSeverity.WARNING,
        translation_key="webhook_unhealthy",
        translation_placeholders={
            "title": entry.title,
            "webhook_id": health.webhook_id,
            "is_enabled": str(health.is_enabled),
            "trigger_count": str(health.trigger_count),
            "data_count": str(health.data_count),
        },
    )


async def async_load_capabilities(
    coordinators: list[SmartcarVehicleCoordinator],
) -> list[SmartcarVehicleCoordinator]:
    """Read each vehicle's capabilities before its entities are created.

    Returns:
        The coordinators that still need a first refresh, because their
        capability read did not happen or did not succeed.
    """
    loaded = await asyncio.gather(
        *[coordinator.async_load_capabilities() for coordinator in coordinators]
    )

    return [
        coordinator
        for coordinator, capabilities_loaded in zip(coordinators, loaded, strict=True)
        if not capabilities_loaded
    ]


async def async_do_first_refresh(coordinator: SmartcarVehicleCoordinator) -> None:
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug(
        "Coordinator created and initial data fetched for %s (VIN: %s)",
        coordinator.vehicle_id,
        coordinator.vin,
    )


async def async_unload_entry(hass: HomeAssistant, entry: SmartcarConfigEntry) -> bool:
    """Unload a config entry.

    Returns:
        If the unload was successful.
    """
    _LOGGER.info("Unloading Smartcar entry %s", entry.entry_id)
    if CONF_WEBHOOK_ID in entry.data:
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))


async def async_remove_entry(hass: HomeAssistant, entry: SmartcarConfigEntry) -> None:
    """Cleanup when entry is removed."""
    if CONF_WEBHOOK_ID in entry.data and (
        cloud.async_active_subscription(hass) or entry.data.get(CONF_CLOUDHOOK, False)
    ):
        try:
            _LOGGER.debug(
                "Removing Smartcar cloudhook (%s)", entry.data[CONF_WEBHOOK_ID]
            )
            await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
        except cloud.CloudNotAvailable:
            pass


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    # a rollback needs no guard here: core refuses to load an entry whose
    # version is higher than the flow's VERSION, logs it, and never calls this
    # function, so the guard that used to live here was unreachable.

    if config_entry.version == 1:
        old_data = config_entry.data
        implementation = cast(
            "LocalOAuth2Implementation",
            await async_get_config_entry_implementation(hass, config_entry),
        )
        session = async_get_clientsession(hass)
        token = old_data[CONF_TOKEN]
        access_token = token[CONF_ACCESS_TOKEN]
        scopes = token["scope"].split(" ")
        auth = AccessTokenAuthImpl(
            session,
            access_token,
            API_ENDPOINTS,
            version=api_version_for_client_id(implementation.client_id),
        )

        # copy old data & remove old keys
        new_data = {**old_data}
        new_data[CONF_TOKEN] = {**old_data[CONF_TOKEN]}
        new_data[CONF_TOKEN].pop("scope", None)

        await populate_entry_data(new_data, auth, scopes)

        old_vehicle_ids = set(old_data.get("vehicles", {}).keys())
        new_vehicle_ids = set(new_data["vehicles"].keys())

        # limit the vehicles in the config entry to whatever was in the previous
        # entry even if the API is returning new items.
        if old_vehicle_ids:
            for vehicle_id in new_vehicle_ids:
                if vehicle_id not in old_vehicle_ids:
                    new_data["vehicles"].pop(vehicle_id, None)

        # ensure all previously accessible vehicles are still accessible.
        inaccessible_vehicle_ids = [
            vehicle_id
            for vehicle_id in old_vehicle_ids
            if vehicle_id not in new_vehicle_ids
        ]

        if inaccessible_vehicle_ids:
            _LOGGER.error(
                "Vehicle(s) are no longer accessible via the API: %s",
                inaccessible_vehicle_ids,
            )
            return False

        hass.config_entries.async_update_entry(
            config_entry,
            unique_id=util.unique_id_from_entry_data(new_data),
            data=new_data,
            version=2,
            minor_version=0,
        )

    if config_entry.minor_version < 1:
        # polling used to be a fixed six hour interval for everyone. the new
        # default is twice a day, which is kinder to the 500 calls per vehicle
        # per month allowance, but changing an existing entry's behaviour
        # silently is not ours to do. write the old cadence explicitly instead,
        # and leave the new default to entries created from now on.
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_POLL_PROFILE: PollProfile.INTERVAL.value,
                CONF_POLL_INTERVAL_HOURS: 6,
                **config_entry.options,
            },
            version=2,
            minor_version=1,
        )

    if config_entry.minor_version < 2:
        # the stored permission list used to be the boxes the user ticked in
        # the config flow, which was never more than a request. Smartcar's own
        # answer is `attributes.permissions` on `GET /connections`, so that is
        # what the entry carries from here on.
        new_data = {**config_entry.data}
        implementation = cast(
            "LocalOAuth2Implementation",
            await async_get_config_entry_implementation(hass, config_entry),
        )
        is_v3 = api_version_for_client_id(implementation.client_id) == "v3"

        if is_v3 and "granted_permissions" not in new_data:
            # an entry old enough to predate the granted list has to ask. the
            # read is free: `/connections` is not addressed to a vehicle, so it
            # does not touch the 500 per vehicle monthly allowance.
            entry_auth = AsyncConfigEntryAuth(
                async_get_clientsession(hass),
                implementation,
                OAuth2Session(hass, config_entry, implementation),
                API_ENDPOINTS,
                user_id=new_data.get("user_id"),
            )

            try:
                new_data["granted_permissions"] = await _read_granted_permissions(
                    entry_auth
                )
            except (ClientResponseError, ClientError):
                # a migration that cannot reach Smartcar must not destroy the
                # entry. Home Assistant retries the setup, and the requested
                # list keeps working until one succeeds.
                _LOGGER.warning(
                    "Could not read the granted permissions for %s; keeping the "
                    "previously requested list until the next setup",
                    config_entry.title,
                )
                return False

        if granted := new_data.get("granted_permissions"):
            new_data["token"] = {**new_data.get("token", {}), "scopes": granted}

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=2,
            minor_version=2,
        )

    _LOGGER.debug(
        "Migration to configuration version %s.%s successful",
        config_entry.version,
        config_entry.minor_version,
    )

    return True


async def _read_granted_permissions(auth: AbstractAuth) -> list[str]:
    """What Smartcar reports it granted, across every connection.

    `/connections` is an application level read, so it is not billed against
    any vehicle's monthly allowance.

    Returns:
        The granted permissions, sorted.
    """
    return _granted_permissions(await _fetch_all_connections(auth))


def _granted_permissions(connections: list[dict]) -> list[str]:
    """Collect `attributes.permissions` from every connection.

    Returns:
        The granted permissions, sorted and deduplicated.
    """
    return sorted(
        {
            permission
            for connection in connections
            for permission in connection.get("attributes", {}).get("permissions", [])
            if isinstance(permission, str)
        }
    )


def vehicle_vins_in_use(
    hass: HomeAssistant, config_entry: ConfigEntry | None = None
) -> set[str]:
    return {
        vehicle["vin"]
        for other_entry in hass.config_entries.async_entries(DOMAIN)
        for vehicle in other_entry.data.get("vehicles", {}).values()
        if vehicle.get("vin")
        and (not config_entry or other_entry.unique_id != config_entry.unique_id)
    }


async def populate_entry_data(
    data: dict,
    auth: AbstractAuth,
    scopes: list[str] | None = None,
) -> None:
    """Populate config entry data during initial creation or migration.

    The permission list is not something this integration picks any more. On v3
    it comes from `GET /connections`, which reports what Smartcar actually
    granted rather than what anybody asked for, and that read is free: it is
    not addressed to a vehicle, so it does not touch the 500 per vehicle
    monthly allowance.

    v2 has no `/connections`, so there the requested list is the only list
    there is and it stands.
    """
    data.setdefault("token", {})["scopes"] = list(scopes or ALL_SCOPES)

    await _store_all_vehicles(data, auth)

    if granted := data.get("granted_permissions"):
        data["token"]["scopes"] = granted


CONNECTIONS_PAGE_SIZE = 100
CONNECTIONS_PAGE_LIMIT = 50


async def _fetch_all_connections(auth: AbstractAuth) -> list[dict]:
    """Read every page of /connections.

    The endpoint is paginated and defaults to 10 per page. Reading only the
    first page silently loses vehicles, and it also makes the single-user check
    decide on a partial set, which would report a multi-user application for an
    account that simply has more than ten connections.

    Any page request that fails raises, because a partial list is worse than
    no list: it silently drops vehicles.

    Returns:
        Every connection resource across all pages.
    """
    connections: list[dict] = []
    page = 1

    while page <= CONNECTIONS_PAGE_LIMIT:
        response = await auth.request_v3(
            "get",
            "connections",
            params={
                "page[number]": page,
                "page[size]": CONNECTIONS_PAGE_SIZE,
            },
        )
        response.raise_for_status()
        body = await response.json()
        connections.extend(body.get("data", []))

        total = body.get("meta", {}).get("totalCount")

        if not body.get("data") or total is None or len(connections) >= total:
            break

        page += 1
    else:
        _LOGGER.warning(
            "Stopped reading connections after %s pages", CONNECTIONS_PAGE_LIMIT
        )

    _LOGGER.debug("Read %s connections across %s page(s)", len(connections), page)

    return connections


async def _store_all_vehicles(
    data: dict,
    auth: AbstractAuth,
) -> None:
    """Fetch and store data for all vehicles in config entry data.

    Raises:
        EmptyVehicleListError: If no vehicles are found.
        MissingRequiredPermissionsError: If a required permission is not granted.
        UnsupportedUserConfigurationError: If there is not exactly 1 user.
        InvalidAuthError: If the request cannot be authorized.
        ClientResponseError: If there is a request error.
    """

    _LOGGER.info("Fetching Smartcar vehicle IDs...")

    data["vehicles"] = {}
    known_details: dict[str, dict] = {}

    try:
        if auth.version == "v2":
            vehicle_list_resp = await auth.request_v2("get", "vehicles")
            vehicle_list_resp.raise_for_status()
            vehicle_list_data = await vehicle_list_resp.json()
            vehicle_ids = vehicle_list_data.get("vehicles", [])
        else:
            assert auth.version == "v3"
            connections = await _fetch_all_connections(auth)
            vehicle_ids = [
                vehicle_id
                for connection in connections
                if (
                    vehicle_id := connection.get("relationships", {})
                    .get("vehicle", {})
                    .get("data", {})
                    .get("id", None)
                )
            ]
            user_ids = {
                user_id
                for connection in connections
                if (
                    user_id := connection.get("relationships", {})
                    .get("user", {})
                    .get("data", {})
                    .get("id", None)
                )
            }

            # check for an empty vehicle list first: with no connections at
            # all, the user count check below would misreport the problem as
            # a multi-user configuration issue.
            if not vehicle_ids:
                raise EmptyVehicleListError

            if len(user_ids) != 1:
                raise UnsupportedUserConfigurationError

            auth.user_id = data["user_id"] = next(iter(user_ids))

            # what Smartcar actually granted, which is not the list the user
            # asked for: a scope can be requested and refused, and a vehicle
            # can lack the capability behind it. Kept so the reconfigure form
            # can start from reality instead of from a static default.
            data["granted_permissions"] = granted = _granted_permissions(connections)

            # without these two there is no way to name a vehicle or tell two
            # of them apart, so there is nothing to set up. Everything else is
            # optional: a permission that was not granted costs its own
            # entities and nothing else.
            if missing := [scope for scope in REQUIRED_SCOPES if scope not in granted]:
                raise MissingRequiredPermissionsError(missing)

            # the connection already describes the car. taking make, model and
            # year from here rather than from a signal response means setup
            # does not depend on the signal store holding anything, and saves
            # a billed request per vehicle.
            known_details = {
                vehicle_id: vehicle
                for connection in connections
                if (
                    vehicle_id := connection.get("relationships", {})
                    .get("vehicle", {})
                    .get("data", {})
                    .get("id", None)
                )
                and (vehicle := connection.get("attributes", {}).get("vehicle"))
            }

    except ClientResponseError as err:
        if err.status == HTTPStatus.UNAUTHORIZED:
            msg = f"Auth error fetching vehicle list: {err.status}"
            raise InvalidAuthError(msg) from err
        raise

    _LOGGER.info("Found %s vehicle IDs", len(vehicle_ids))

    if not vehicle_ids:
        raise EmptyVehicleListError

    await asyncio.gather(
        *[
            _store_vehicle_details(data, auth, vid, known_details.get(vid))
            for vid in vehicle_ids
        ]
    )


async def _fetch_vin(auth: AbstractAuth, vehicle_id: str) -> tuple[str | None, dict]:
    """Read the VIN signal, tolerating a vehicle that has no stored signals.

    A vehicle whose signal store is empty answers `404 SIGNAL_NOT_FOUND` here,
    and that is not a reason to refuse to set the vehicle up: the VIN is used
    to notice the same car configured twice, which is worth having and worth
    doing without. Observed on a live account, so it is the ordinary case for
    a vehicle that has never been collected from rather than an edge case.

    Returns:
        The VIN if there is one, and whatever the response said about the
        vehicle itself.
    """
    _LOGGER.debug("Fetching VIN for vehicle ID: %s", vehicle_id)

    response = await auth.request_v3(
        "get", f"vehicles/{vehicle_id}/signals/vehicleidentification-vin"
    )

    if response.status == HTTPStatus.NOT_FOUND:
        _LOGGER.info(
            "Vehicle %s has no stored VIN signal; continuing without it",
            vehicle_id,
        )
        return None, {}

    response.raise_for_status()
    body = await response.json()

    return (
        body.get("data", {}).get("attributes", {}).get("body", {}).get("value"),
        body.get("included", {}).get("vehicle", {}).get("attributes", {}),
    )


async def _fetch_vehicle_attributes(auth: AbstractAuth, vehicle_id: str) -> dict:
    """Read make, model, year and powertrain from the vehicle endpoint.

    The last resort, for a vehicle that neither the connection nor the VIN
    signal described. Unlike the signal endpoints this one answers for a
    vehicle with nothing collected yet.

    Returns:
        The vehicle attributes, empty if the request did not carry any.
    """
    _LOGGER.debug("Fetching attributes for vehicle ID: %s", vehicle_id)

    response = await auth.request_v3("get", f"vehicles/{vehicle_id}")
    response.raise_for_status()
    body = await response.json()
    attributes: dict = body.get("data", {}).get("attributes", {})

    return attributes


async def _store_vehicle_details(
    data: dict,
    auth: AbstractAuth,
    vehicle_id: str,
    known_details: dict | None = None,
) -> None:
    """Fetch and store data for a single vehicle.

    Raises:
        InvalidAuthError: If the request cannot be authorized.
        ClientResponseError: If there is a request error.
    """
    vehicle_info: dict = {}
    vin = None

    try:
        if auth.version == "v2":
            _LOGGER.debug("Fetching VIN for vehicle ID: %s", vehicle_id)
            vin_resp = await auth.request_v2("get", f"vehicles/{vehicle_id}/vin")
            vin_resp.raise_for_status()
            vin_data = await vin_resp.json()
            vin = vin_data.get("vin")
        else:
            assert auth.version == "v3"
            vin, from_signal = await _fetch_vin(auth, vehicle_id)
            vehicle_info = dict(known_details or {}) or from_signal

            if not vehicle_info:
                vehicle_info = await _fetch_vehicle_attributes(auth, vehicle_id)

        if auth.version == "v2":
            _LOGGER.debug("Fetching attributes for vehicle ID: %s", vehicle_id)
            attr_resp = await auth.request_v2("get", f"vehicles/{vehicle_id}")
            attr_resp.raise_for_status()
            vehicle_info = await attr_resp.json()

        make = vehicle_info.get("make")
        model = vehicle_info.get("model")
        year = str(vehicle_info.get("year"))

        data["vehicles"][vehicle_id] = {
            "make": make,
            "model": model,
            "year": year,
        }

        if vin:
            data["vehicles"][vehicle_id]["vin"] = vin

    except ClientResponseError as err:
        if err.status == HTTPStatus.UNAUTHORIZED:
            msg = f"Auth error [{err.status}] during vehicle setup"
            raise InvalidAuthError(msg) from err
        raise
