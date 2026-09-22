"""Diagnostics support for Smartcar."""

from http import HTTPStatus
import json
from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_WEBHOOK_ID,
)
from homeassistant.core import HomeAssistant

from .const import CONF_APPLICATION_MANAGEMENT_TOKEN
from .coordinator import SmartcarVehicleCoordinator
from .management import webhook_health, webhook_id_matching_url
from .webhooks import webhook_url_from_id

CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105
CONF_VIN = "vin"

TO_REDACT = {
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_ACCESS_TOKEN,
    CONF_APPLICATION_MANAGEMENT_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_VIN,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinators: dict[str, SmartcarVehicleCoordinator] = (
        entry.runtime_data.coordinators
    )

    meta_coordinator = entry.runtime_data.meta_coordinator
    metadata = {**meta_coordinator.data}

    if "last_webhook_request" in metadata:
        include_raw = False
        response = metadata.get("last_webhook_response", {})
        response_status = response.get("status")
        request = metadata.pop("last_webhook_request")
        include_raw = response_status == HTTPStatus.UNAUTHORIZED

        try:
            metadata["last_webhook_request"] = json.loads(request)
        except json.JSONDecodeError:
            include_raw = True

        if include_raw:
            metadata["last_webhook_request_raw"] = request

    last_webhook_meta = None
    last_request = metadata.get("last_webhook_request")

    if isinstance(last_request, dict):
        request_meta = last_request.get("meta", {})
        last_webhook_meta = {
            "mode": request_meta.get("mode"),
            "deliveredAt": request_meta.get("deliveredAt"),
            "signalCount": request_meta.get("signalCount"),
            "triggerCodes": [
                trigger_signal.get("code")
                for trigger in last_request.get("triggers", [])
                if isinstance(trigger, dict)
                and isinstance(trigger_signal := trigger.get("signal"), dict)
            ],
        }

    webhook_url = None
    webhook_health_summary = None

    if CONF_WEBHOOK_ID in entry.data:
        webhook_url = (await webhook_url_from_id(hass, entry.data[CONF_WEBHOOK_ID]))[0]
        webhook_health_summary = await _webhook_health_summary(entry, webhook_url)

    return cast(
        "dict[str, Any]",
        async_redact_data(
            {
                "entry": entry.as_dict(),
                "webhook_url": webhook_url,
                "webhook_health": webhook_health_summary,
                "last_webhook_meta": last_webhook_meta,
                "data": {
                    coordinator_name: coordinator.data
                    for coordinator_name, coordinator in coordinators.items()
                },
                "signal_store": {
                    coordinator_name: {
                        "totalCount": coordinator.last_poll_total_count,
                        "unmappedCodes": coordinator.last_poll_unmapped_codes,
                    }
                    for coordinator_name, coordinator in coordinators.items()
                },
                "version": next(
                    iter(coordinator.version for coordinator in coordinators.values())
                ),
                "metadata": metadata,
            },
            TO_REDACT,
        ),
    )


async def _webhook_health_summary(
    entry: ConfigEntry, webhook_url: str
) -> dict[str, Any] | None:
    """Best effort webhook health, for a diagnostics dump only.

    Never allowed to fail the diagnostics download over a Management API
    hiccup, so any error here is swallowed and reported as `None` instead.

    Returns:
        The health summary, or None if it could not be read.
    """
    try:
        management = entry.runtime_data.management
        webhooks = await management.async_list_webhooks()
        target_webhook = webhook_id_matching_url(webhooks, webhook_url)

        if target_webhook is None:
            return {"matched": False}

        health = webhook_health(webhooks, target_webhook)
    except Exception:  # noqa: BLE001
        return None

    if health is None:
        return {"matched": True, "found": False}

    return {
        "matched": True,
        "found": True,
        "isEnabled": health.is_enabled,
        "triggerCount": health.trigger_count,
        "dataCount": health.data_count,
        "isHealthy": health.is_healthy,
    }
