from collections.abc import Callable
from functools import wraps
import hmac
from http import HTTPStatus
import json
import logging
from typing import Any, cast

from aiohttp import web
from homeassistant.components import cloud, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import events, util
from .const import CONF_APPLICATION_MANAGEMENT_TOKEN
from .coordinator import SmartcarVehicleCoordinator, _is_integrated
from .types import SmartcarData

_LOGGER = logging.getLogger(__name__)


async def webhook_url_from_id(hass: HomeAssistant, webhook_id: str) -> tuple[str, bool]:
    if cloud.async_active_subscription(hass):
        webhook_url = await cloud.async_get_or_create_cloudhook(hass, webhook_id)
        cloudhook = True
    else:
        webhook_url = webhook.async_generate_url(hass, webhook_id)
        cloudhook = False

    return webhook_url, cloudhook


def update_meta_coordinator_data[F: Callable[..., Any], ReturnT](fn: F) -> F:
    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> ReturnT:  # noqa: ANN401
        response = await fn(*args, **kwargs)
        config_entry = kwargs["config_entry"]
        request = args[2]
        status = response.status
        data = response.text or (response.body and response.body.decode("utf-8"))
        meta_coordinator = config_entry.runtime_data.meta_coordinator
        meta_coordinator.async_set_updated_data(
            {
                **meta_coordinator.data,
                "last_webhook_received_at": dt_util.utcnow(),
                "last_webhook_response": {
                    "status": status,
                    **({"data": data} if data else {}),
                },
                "last_webhook_request": await request.text(),
            }
        )
        return cast("ReturnT", response)

    return cast("F", wrapper)


@update_meta_coordinator_data
async def handle_webhook(
    hass: HomeAssistant,
    webhook_id: str,  # noqa: ARG001
    request: web.Request,
    *,
    config_entry: ConfigEntry,
) -> web.Response:
    """Handle webhook callback.

    Returns:
        The response to send back to Smartcar.
    """
    try:
        body = await request.text()
        message = json.loads(body)
    except ValueError:
        _LOGGER.warning("Received invalid JSON from Smartcar")
        return web.json_response(
            {
                "error": {
                    "code": "invalid_json",
                    "message": "invalid JSON body",
                }
            },
            status=HTTPStatus.BAD_REQUEST,
        )

    _LOGGER.debug("Received JSON from Smartcar: %r", body)

    app_token: str = config_entry.data[CONF_APPLICATION_MANAGEMENT_TOKEN]
    signature = request.headers.get("SC-Signature")
    data = message.get("data", {})
    meta = message.get("meta", {})
    delivery_id = meta.get("deliveryId")

    if message.get("eventType") == "VERIFY":
        # never log the management token or the computed challenge response;
        # that a challenge was answered is all a troubleshooting session
        # needs to know.
        _LOGGER.debug("Answered Smartcar VERIFY challenge (deliveryId=%s)", delivery_id)
        return web.json_response(
            {"challenge": util.hmac_sha256_hexdigest(app_token, data["challenge"])}
        )

    _LOGGER.debug("Validating signature")

    # the verify message is not signed, so that's done before this check. all
    # other messages must be signed & validated before we process the data from
    # them.
    if signature is None or not hmac.compare_digest(
        util.hmac_sha256_hexdigest(app_token, body), signature
    ):
        # the delivery id is the only detail worth keeping here: it is not
        # secret, and it is the one thing that lets Sean match a rejected
        # delivery in these logs against the same delivery in the Smartcar
        # dashboard. never log the signature header or the token used to
        # compute the expected one.
        _LOGGER.warning(
            "Ignoring webhook message with an invalid signature (deliveryId=%s)",
            delivery_id,
        )
        return web.json_response(
            {
                "error": {
                    "code": "invalid_signature",
                    "message": "invalid signature on request body",
                }
            },
            status=HTTPStatus.UNAUTHORIZED,
        )

    _log_webhook_summary(message)

    # respond to test mode payloads to aid with setup
    if message.get("meta", {}).get("mode") == "TEST":
        vehicle = data.get("vehicle", {})
        vehicle_id = vehicle.get("id")
        _LOGGER.debug(
            "mode=TEST; no action taken for vehicle with id: %s",
            vehicle_id,
        )
        return web.json_response(
            {
                "status": {
                    "code": "acknowledged",
                    "message": "no action taken; (mode=TEST)",
                },
                "vehicle": vehicle,
            },
            status=HTTPStatus.ACCEPTED,
        )

    errors = data.get("errors", [])
    signals = data.get("signals", [])
    vehicle = data.get("vehicle", {})
    vehicle_id = vehicle.get("id")
    runtime_data: SmartcarData = config_entry.runtime_data
    coordinators = runtime_data.coordinators
    vehicle_vin: str | None = next(
        (
            vin
            for coordinator in coordinators.values()
            if (
                vin := coordinator.entry.data.get("vehicles", {})
                .get(vehicle_id, {})
                .get("vin")
            )
        ),
        None,
    )
    coordinator = coordinators.get(vehicle_id) or (
        coordinators.get(vehicle_vin) if vehicle_vin else None
    )

    if not coordinator:
        _LOGGER.debug(
            "ignoring message for unknown vehicle with id: %s, vin: %s",
            vehicle_id,
            vehicle_vin or "unknown",
        )
        return web.json_response(
            {
                "error": {
                    "code": "unknown_vehicle",
                    "message": "unknown vehicle included",
                }
            },
            status=HTTPStatus.CONFLICT,
        )

    # mode=TEST deliveries returned earlier without reaching here, so any
    # delivery that gets this far is a live one.
    coordinator.note_live_webhook()

    _fire_webhook_received(hass, coordinator, message, errors)
    _handle_webhook_errors(coordinator, errors)
    _handle_webhook_signals(coordinator, signals)

    return web.Response(status=HTTPStatus.NO_CONTENT)


def _fire_webhook_received(
    hass: HomeAssistant,
    coordinator: SmartcarVehicleCoordinator,
    message: dict,
    errors: list[dict],
) -> None:
    """Fire `smartcar_webhook_received` for a signed, validated delivery."""
    meta = message.get("meta", {})
    trigger_codes: list[str] = [
        str(trigger_signal.get("code"))
        for trigger in message.get("triggers", [])
        if isinstance(trigger, dict)
        and isinstance(trigger_signal := trigger.get("signal"), dict)
        and trigger_signal.get("code") is not None
    ]
    error_codes = [
        f"{error.get('type')}:{error.get('code')}"
        for error in errors
        if isinstance(error, dict) and error.get("type") and error.get("code")
    ]

    events.fire_webhook_received(
        hass,
        entry_id=coordinator.entry.entry_id,
        vehicle_id=coordinator.vehicle_id,
        identifier_key=coordinator.identifier_key,
        event_type=str(message.get("eventType")),
        mode=meta.get("mode"),
        delivery_id=meta.get("deliveryId"),
        sequence=meta.get("sequence"),
        signal_count=meta.get("signalCount"),
        trigger_codes=trigger_codes,
        error_codes=error_codes,
    )


def _log_webhook_summary(message: dict) -> None:
    """Log one structured line describing a signed, validated delivery.

    This is the line a troubleshooting session actually wants: what kind of
    event this was, which vehicle, which triggers fired, and (for an error
    delivery) what Smartcar said was wrong, all in one place instead of
    scattered across the raw body dump above it.
    """
    data = message.get("data", {})
    meta = message.get("meta", {})
    event_type = message.get("eventType")
    vehicle_id = data.get("vehicle", {}).get("id")
    trigger_codes = [
        trigger_signal.get("code")
        for trigger in message.get("triggers", [])
        if isinstance(trigger, dict)
        and isinstance(trigger_signal := trigger.get("signal"), dict)
    ]

    _LOGGER.debug(
        "Webhook delivery: eventType=%s mode=%s version=%s deliveryId=%s "
        "sequence=%s signalCount=%s vehicle=%s triggers=%s",
        event_type,
        meta.get("mode"),
        meta.get("version"),
        meta.get("deliveryId"),
        meta.get("sequence"),
        meta.get("signalCount"),
        vehicle_id,
        trigger_codes,
    )

    if event_type == "VEHICLE_ERROR":
        for error in data.get("errors", []):
            if not isinstance(error, dict):
                continue

            error_signal_codes = [
                signal.get("code")
                for signal in error.get("signals", [])
                if isinstance(signal, dict)
            ]

            _LOGGER.debug(
                "Webhook error: type=%s code=%s resolution=%s signals=%s",
                error.get("type"),
                error.get("code"),
                error.get("resolution", {}).get("type"),
                error_signal_codes,
            )


def _handle_webhook_errors(
    coordinator: SmartcarVehicleCoordinator,
    errors: list[dict],
) -> None:
    hass = coordinator.hass
    config_entry = coordinator.entry

    for error in errors:
        error_type = error.get("type")
        resolution = error.get("resolution", {}).get("type")
        signals = error.get("signals", [])
        if (
            error_type == "PERMISSION"
            and resolution == "REAUTHENTICATE"
            and (not signals or any(_is_integrated(s) for s in signals))
        ):
            _LOGGER.info("requesting reauth due to webhook message: %s", error)
            config_entry.async_start_reauth(hass)
        else:
            _LOGGER.debug("ignoring error in webhook: %s", error)


def _handle_webhook_signals(
    coordinator: SmartcarVehicleCoordinator,
    signals: list[dict],
) -> None:
    if signals:
        # a VEHICLE_STATE delivery with at least one signal is proof the
        # store is no longer empty, whatever the trigger (including the
        # FIRST_DELIVERY that opens a fresh subscription).
        coordinator.clear_empty_store_issue()

    with coordinator.create_updated_data() as (add, updated_data):
        for signal in signals:
            try:
                add.from_signal_attributes(signal)
            except Exception:
                # one malformed signal (an unexpected shape, a bad timestamp)
                # must not lose every other signal in the same VEHICLE_STATE
                # delivery; log it and keep going.
                _LOGGER.exception(
                    "Ignoring malformed signal in webhook payload: %r", signal
                )

        if add.addition_made:
            coordinator.async_set_updated_data(updated_data)
