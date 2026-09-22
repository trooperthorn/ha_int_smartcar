"""Bus events fired by the Smartcar integration.

These give automations and phones a way to close the loop on commands and
problems without polling entity state: a command result the moment it is
known (including a slow `202` whose real outcome streams in later), a webhook
delivery summary, and the repair issues this integration raises or clears.

None of these events ever carry a token, a signature, or a full callback URL;
`_redact_placeholders` strips anything that could leak Nabu Casa cloudhook
credentials down to a bare host.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from .const import DOMAIN

EVENT_COMMAND_RESULT = f"{DOMAIN}_command_result"
EVENT_WEBHOOK_RECEIVED = f"{DOMAIN}_webhook_received"
EVENT_ISSUE_RAISED = f"{DOMAIN}_issue_raised"
EVENT_ISSUE_CLEARED = f"{DOMAIN}_issue_cleared"

# placeholder keys whose value is a URL that may embed a Nabu Casa cloudhook
# token; these are reduced to just the host before an event carries them.
_URL_PLACEHOLDER_KEYS = ("callback_url", "docs_url")
_CLOUDHOOK_HOST_MARKER = "hooks.nabu.casa"


def _device_id_for(
    hass: HomeAssistant, entry_id: str, identifier_key: str
) -> str | None:
    """Look up the device registry id for a vehicle.

    Returns:
        The device id, or None if the device is not registered.
    """
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier_key), entry_id
    )
    return device.id if device else None


def _redact_placeholders(placeholders: dict[str, Any] | None) -> dict[str, Any]:
    """Strip tokens out of translation placeholders bound for an event.

    A callback/docs URL placeholder is reduced to its host whenever it looks
    like it could carry a Nabu Casa cloudhook token; every other placeholder
    passes through unchanged.

    Returns:
        A redacted copy of the placeholders, never the original object.
    """
    if not placeholders:
        return {}

    redacted: dict[str, Any] = dict(placeholders)

    for key in _URL_PLACEHOLDER_KEYS:
        value = redacted.get(key)
        if isinstance(value, str) and _CLOUDHOOK_HOST_MARKER in value:
            redacted[key] = urlparse(value).hostname or _CLOUDHOOK_HOST_MARKER

    return redacted


def fire_command_result(
    hass: HomeAssistant,
    *,
    entry_id: str,
    vehicle_id: str,
    vin: str | None,
    identifier_key: str,
    command: str,
    success: bool,
    took_ms: int,
    http_status: int | None = None,
    error_type: str | None = None,
    error_code: str | None = None,
    resolution_type: str | None = None,
    suggested_user_message: str | None = None,
) -> None:
    """Fire `smartcar_command_result` after a command finishes.

    Fired for every command, whatever the outcome, including a `202` whose
    streamed body later reports a failure.
    """
    hass.bus.async_fire(
        EVENT_COMMAND_RESULT,
        {
            "entry_id": entry_id,
            "vehicle_id": vehicle_id,
            "vin": vin,
            "device_id": _device_id_for(hass, entry_id, identifier_key),
            "command": command,
            "success": success,
            "http_status": http_status,
            "error_type": error_type,
            "error_code": error_code,
            "resolution_type": resolution_type,
            "suggested_user_message": suggested_user_message,
            "took_ms": took_ms,
        },
    )


def fire_webhook_received(
    hass: HomeAssistant,
    *,
    entry_id: str,
    vehicle_id: str,
    identifier_key: str,
    event_type: str,
    mode: str | None,
    delivery_id: str | None,
    sequence: int | None,
    signal_count: int | None,
    trigger_codes: list[str],
    error_codes: list[str],
) -> None:
    """Fire `smartcar_webhook_received` for every validated delivery."""
    hass.bus.async_fire(
        EVENT_WEBHOOK_RECEIVED,
        {
            "vehicle_id": vehicle_id,
            "device_id": _device_id_for(hass, entry_id, identifier_key),
            "event_type": event_type,
            "mode": mode,
            "delivery_id": delivery_id,
            "sequence": sequence,
            "signal_count": signal_count,
            "trigger_codes": trigger_codes,
            "error_codes": error_codes,
        },
    )


def fire_issue_raised(
    hass: HomeAssistant,
    *,
    issue_id: str,
    translation_key: str,
    severity: str,
    entry_id: str,
    placeholders: dict[str, Any] | None = None,
) -> None:
    """Fire `smartcar_issue_raised` when a repair issue is created."""
    hass.bus.async_fire(
        EVENT_ISSUE_RAISED,
        {
            "issue_id": issue_id,
            "translation_key": translation_key,
            "severity": severity,
            "entry_id": entry_id,
            "placeholders": _redact_placeholders(placeholders),
        },
    )


def fire_issue_cleared(
    hass: HomeAssistant,
    *,
    issue_id: str,
    translation_key: str,
    severity: str,
    entry_id: str,
) -> None:
    """Fire `smartcar_issue_cleared` when a repair issue is deleted."""
    hass.bus.async_fire(
        EVENT_ISSUE_CLEARED,
        {
            "issue_id": issue_id,
            "translation_key": translation_key,
            "severity": severity,
            "entry_id": entry_id,
        },
    )


def create_issue(
    hass: HomeAssistant,
    *,
    issue_id: str,
    entry_id: str,
    is_fixable: bool,
    is_persistent: bool,
    severity: ir.IssueSeverity,
    translation_key: str,
    translation_placeholders: dict[str, str] | None = None,
) -> None:
    """Create a repair issue and fire `smartcar_issue_raised` for it.

    The one call site every part of this integration that raises one of its
    own repair issues should use, so the event and the issue can never drift
    apart.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=is_fixable,
        is_persistent=is_persistent,
        severity=severity,
        translation_key=translation_key,
        translation_placeholders=translation_placeholders,
    )
    fire_issue_raised(
        hass,
        issue_id=issue_id,
        translation_key=translation_key,
        severity=severity.value,
        entry_id=entry_id,
        placeholders=translation_placeholders,
    )


def delete_issue(
    hass: HomeAssistant,
    *,
    issue_id: str,
    entry_id: str,
    translation_key: str,
    severity: ir.IssueSeverity,
) -> None:
    """Delete a repair issue and fire `smartcar_issue_cleared` if it existed.

    The event only fires when the issue was actually present, so a call site
    that deletes defensively on every pass does not spam an event for an
    issue nobody ever saw.
    """
    existed = ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    ir.async_delete_issue(hass, DOMAIN, issue_id)

    if existed:
        fire_issue_cleared(
            hass,
            issue_id=issue_id,
            translation_key=translation_key,
            severity=severity.value,
            entry_id=entry_id,
        )
