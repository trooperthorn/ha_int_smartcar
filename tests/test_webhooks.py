"""Test webhook authentication and logging."""

import json
import logging

from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    load_json_object_fixture,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.smartcar import webhooks as webhooks_module
from custom_components.smartcar.const import CONF_APPLICATION_MANAGEMENT_TOKEN, DOMAIN
from custom_components.smartcar.util import hmac_sha256_hexdigest

from . import setup_added_integration


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("valid_signature", [True, False])
async def test_webhook_does_not_log_management_token(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
    caplog: pytest.LogCaptureFixture,
    valid_signature: bool,
) -> None:
    """Never log the signing secret, even for an invalid signature."""
    token = "synthetic-management-token-for-log-regression"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_log_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    body = json.dumps(
        {
            "eventType": "VEHICLE_STATE",
            "data": {"vehicle": {"id": vehicle_attributes["id"]}, "signals": []},
        }
    )
    signature = hmac_sha256_hexdigest(token, body) if valid_signature else "invalid"
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="custom_components.smartcar.webhooks"):
        client = await hass_client()
        response = await client.post(
            "/api/webhook/smartcar_log_test",
            data=body,
            headers={"Content-Type": "application/json", "SC-Signature": signature},
        )
        await hass.async_block_till_done()

    assert response.status == (204 if valid_signature else 401)
    assert "Validating" in caplog.text
    assert token not in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_dashboard_sample_test_mode_is_acknowledged_without_a_state_change(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
) -> None:
    """A dashboard "Send test event" (`meta.mode: TEST`) never touches state.

    This is the exact payload shape Sean's Smartcar dashboard sends: unix
    millisecond timestamps, a top level `triggers` array shaped
    `{type, signal: {name, code, group}}` rather than the flat `{type,
    signal}` the prose docs show, and `body.values` (plural, an array) on
    `vehicleuseraccount-permissions`.
    """
    token = "dashboard-sample-management-token"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_dashboard_sample_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    payload = dict(
        load_json_object_fixture("webhooks/dashboard_sample_state.json", DOMAIN)
    )
    payload["data"]["vehicle"]["id"] = vehicle_attributes["id"]
    body = json.dumps(payload)
    signature = hmac_sha256_hexdigest(token, body)

    battery_before = hass.states.get("sensor.vw_id_4_battery")

    client = await hass_client()
    response = await client.post(
        "/api/webhook/smartcar_dashboard_sample_test",
        data=body,
        headers={"Content-Type": "application/json", "SC-Signature": signature},
    )
    await hass.async_block_till_done()

    assert response.status == 202
    assert hass.states.get("sensor.vw_id_4_battery") == battery_before


@pytest.mark.usefixtures("enable_all_entities")
@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_dashboard_sample_live_delivery_updates_entities(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
) -> None:
    """The same sample payload, delivered live, must actually update state."""
    token = "dashboard-sample-management-token-live"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_dashboard_sample_live",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    payload = dict(
        load_json_object_fixture("webhooks/dashboard_sample_state.json", DOMAIN)
    )
    payload["data"]["vehicle"]["id"] = vehicle_attributes["id"]
    payload["meta"] = {**payload["meta"], "mode": "LIVE"}
    body = json.dumps(payload)
    signature = hmac_sha256_hexdigest(token, body)

    client = await hass_client()
    response = await client.post(
        "/api/webhook/smartcar_dashboard_sample_live",
        data=body,
        headers={"Content-Type": "application/json", "SC-Signature": signature},
    )
    await hass.async_block_till_done()

    assert response.status == 204

    battery = hass.states.get("sensor.vw_id_4_battery")
    assert battery is not None
    assert battery.state == "78"

    lock = hass.states.get("lock.vw_id_4_door_lock")
    assert lock is not None
    assert lock.state == "locked"

    odometer = hass.states.get("sensor.vw_id_4_odometer")
    assert odometer is not None
    assert odometer.state == "78432"

    # oemUpdatedAt: 0 on the VIN signal is Smartcar's own "no data" marker,
    # not epoch. It must never surface as an `age` attribute anywhere, which
    # it would if 0 were parsed as 1970-01-01 instead of treated as missing.
    assert not any(
        state.attributes.get("age") == "1970-01-01T00:00:00+00:00"
        for entity_id in hass.states.async_entity_ids()
        if (state := hass.states.get(entity_id)) is not None
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_dashboard_sample_vehicle_error_is_accepted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
) -> None:
    """A VEHICLE_ERROR event carries `data.errors`, no top level `triggers`.

    It must be accepted like any other signed delivery rather than rejected
    for lacking the shape a VEHICLE_STATE event has.
    """
    token = "dashboard-sample-management-token-error"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_dashboard_sample_error",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    payload = dict(
        load_json_object_fixture("webhooks/dashboard_sample_error.json", DOMAIN)
    )
    payload["data"]["vehicle"]["id"] = vehicle_attributes["id"]
    payload["meta"] = {**payload["meta"], "mode": "LIVE"}
    body = json.dumps(payload)
    signature = hmac_sha256_hexdigest(token, body)

    client = await hass_client()
    response = await client.post(
        "/api/webhook/smartcar_dashboard_sample_error",
        data=body,
        headers={"Content-Type": "application/json", "SC-Signature": signature},
    )
    await hass.async_block_till_done()

    assert response.status == 204


def test_log_webhook_summary_skips_non_dict_errors() -> None:
    """A malformed `errors` entry must not crash the summary logger."""
    webhooks_module._log_webhook_summary(
        {
            "eventType": "VEHICLE_ERROR",
            "data": {
                "vehicle": {"id": "veh_1"},
                "errors": [
                    None,
                    {
                        "type": "COMPATIBILITY",
                        "code": "VEHICLE_NOT_CAPABLE",
                        "resolution": {"type": None},
                        "signals": [{"code": "closure-islocked"}],
                    },
                ],
            },
            "meta": {},
        }
    )
