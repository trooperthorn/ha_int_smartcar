"""Tests for the `smartcar_*` bus events.

Covers the payload shape of each event, that none of them ever carry a
secret (token, signature, or a full cloudhook URL), and that a command
result fires even when the failure only shows up in a streamed `202` body.
"""

from http import HTTPStatus
import json

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.smartcar import events, webhooks as webhooks_module
from custom_components.smartcar.const import CONF_APPLICATION_MANAGEMENT_TOKEN, DOMAIN
from custom_components.smartcar.entity import async_send_command
from custom_components.smartcar.types import SmartcarAPIError
from custom_components.smartcar.util import hmac_sha256_hexdigest

from . import MOCK_API_ENDPOINT, setup_added_integration, setup_integration


def _catch(hass: HomeAssistant, event_type: str) -> list[Event]:
    caught: list[Event] = []
    hass.bus.async_listen(event_type, caught.append)
    return caught


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_command_result_fires_on_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
) -> None:
    """A clean command fires `smartcar_command_result` with success=True."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinators[vehicle["id"]]
    caught = _catch(hass, events.EVENT_COMMAND_RESULT)

    aioclient_mock.post(
        f"{MOCK_API_ENDPOINT}/vehicles/{vehicle['id']}/commands/security/lock",
        status=HTTPStatus.OK,
        text=None,
    )

    assert (
        await async_send_command(coordinator, "/security/lock", None, command="lock")
        is True
    )
    await hass.async_block_till_done()

    assert len(caught) == 1
    data = caught[0].data
    assert data["command"] == "lock"
    assert data["success"] is True
    assert data["http_status"] == HTTPStatus.OK
    assert data["error_type"] is None
    assert data["vehicle_id"] == vehicle["id"]
    assert data["vin"] == coordinator.vin
    assert isinstance(data["took_ms"], int)
    assert data["took_ms"] >= 0

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, coordinator.identifier_key), mock_config_entry.entry_id
    )
    assert data["device_id"] == device.id

    # never a token, a signature, or an auth header.
    for value in data.values():
        assert "Bearer" not in str(value)
        assert "authorization" not in str(value).lower()


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_command_result_fires_on_202_streamed_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
) -> None:
    """A `202` whose body reports failure still fires success=False."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinators[vehicle["id"]]
    caught = _catch(hass, events.EVENT_COMMAND_RESULT)

    aioclient_mock.post(
        f"{MOCK_API_ENDPOINT}/vehicles/{vehicle['id']}/commands/security/lock",
        status=HTTPStatus.ACCEPTED,
        json={"type": "VEHICLE_STATE", "code": "UNREACHABLE", "status": 409},
    )

    with pytest.raises(SmartcarAPIError):
        await async_send_command(coordinator, "/security/lock", None, command="lock")
    await hass.async_block_till_done()

    assert len(caught) == 1
    data = caught[0].data
    assert data["command"] == "lock"
    assert data["success"] is False
    assert data["http_status"] == HTTPStatus.CONFLICT
    assert data["error_type"] == "VEHICLE_STATE"
    assert data["error_code"] == "UNREACHABLE"
    assert data["suggested_user_message"] == "UNREACHABLE"


@pytest.mark.parametrize("vehicle_fixture", ["unknown_make"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_command_result_fires_on_http_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    vehicle: dict,
) -> None:
    """A plain HTTP failure status also fires success=False."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinators[vehicle["id"]]
    caught = _catch(hass, events.EVENT_COMMAND_RESULT)

    aioclient_mock.post(
        f"{MOCK_API_ENDPOINT}/vehicles/{vehicle['id']}/commands/security/lock",
        status=HTTPStatus.CONFLICT,
        text="",
    )

    with pytest.raises(SmartcarAPIError):
        await async_send_command(coordinator, "/security/lock", None, command="lock")
    await hass.async_block_till_done()

    assert len(caught) == 1
    data = caught[0].data
    assert data["success"] is False
    assert data["http_status"] == HTTPStatus.CONFLICT
    assert data["error_type"] == "http_error"
    assert data["error_code"] == str(HTTPStatus.CONFLICT.value)


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_webhook_received_event_fires_with_no_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
) -> None:
    """A validated delivery fires `smartcar_webhook_received`, tokens excluded."""

    token = "synthetic-management-token-for-events-test"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "webhook_id": "smartcar_events_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)
    caught = _catch(hass, events.EVENT_WEBHOOK_RECEIVED)

    body = json.dumps(
        {
            "eventType": "VEHICLE_STATE",
            "meta": {
                "mode": "LIVE",
                "deliveryId": "delivery-1",
                "sequence": 3,
                "signalCount": 1,
            },
            "triggers": [{"type": "SIGNAL", "signal": {"code": "closure-islocked"}}],
            "data": {
                "vehicle": {"id": vehicle_attributes["id"]},
                "signals": [
                    {"code": "closure-islocked", "body": {"value": True}, "meta": {}}
                ],
            },
        }
    )
    signature = hmac_sha256_hexdigest(token, body)

    client = await hass_client()
    response = await client.post(
        "/api/webhook/smartcar_events_test",
        data=body,
        headers={"Content-Type": "application/json", "SC-Signature": signature},
    )
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.NO_CONTENT
    assert len(caught) == 1
    data = caught[0].data
    assert data["vehicle_id"] == vehicle_attributes["id"]
    assert data["event_type"] == "VEHICLE_STATE"
    assert data["mode"] == "LIVE"
    assert data["delivery_id"] == "delivery-1"
    assert data["sequence"] == 3
    assert data["signal_count"] == 1
    assert data["trigger_codes"] == ["closure-islocked"]
    assert data["error_codes"] == []

    for value in data.values():
        assert token not in str(value)
        assert signature not in str(value)


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
async def test_webhook_received_event_lists_error_codes(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    vehicle_attributes: dict,
) -> None:
    """A VEHICLE_ERROR delivery reports `type:code` pairs for its errors."""

    token = "synthetic-management-token-for-events-error-test"  # noqa: S105
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "webhook_id": "smartcar_events_error_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: token,
        },
    )
    await setup_added_integration(hass, mock_config_entry)
    caught = _catch(hass, events.EVENT_WEBHOOK_RECEIVED)

    body = json.dumps(
        {
            "eventType": "VEHICLE_ERROR",
            "meta": {"mode": "LIVE", "deliveryId": "delivery-2"},
            "data": {
                "vehicle": {"id": vehicle_attributes["id"]},
                "errors": [
                    {
                        "type": "PERMISSION",
                        "code": "MISSING_PERMISSION",
                        "resolution": {"type": "RECONNECT"},
                        "signals": [],
                    }
                ],
            },
        }
    )
    signature = hmac_sha256_hexdigest(token, body)

    client = await hass_client()
    await client.post(
        "/api/webhook/smartcar_events_error_test",
        data=body,
        headers={"Content-Type": "application/json", "SC-Signature": signature},
    )
    await hass.async_block_till_done()

    assert len(caught) == 1
    assert caught[0].data["error_codes"] == ["PERMISSION:MISSING_PERMISSION"]


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_issue_raised_and_cleared_events(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """`_update_empty_store_issue` raises and clears via the shared helper."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    raised = _catch(hass, events.EVENT_ISSUE_RAISED)
    cleared = _catch(hass, events.EVENT_ISSUE_CLEARED)

    coordinator._update_empty_store_issue(has_signals=False)
    await hass.async_block_till_done()

    assert len(raised) == 1
    raised_data = raised[0].data
    issue_id = (
        f"empty_signal_store_{mock_config_entry.entry_id}_{coordinator.vehicle_id}"
    )
    assert raised_data["issue_id"] == issue_id
    assert raised_data["translation_key"] == "empty_signal_store"
    assert raised_data["severity"] == ir.IssueSeverity.WARNING.value
    assert raised_data["entry_id"] == mock_config_entry.entry_id
    assert raised_data["placeholders"]["vehicle_name"]

    webhooks_module._handle_webhook_signals(
        coordinator,
        [{"code": "closure-islocked", "body": {"value": True}, "meta": {}}],
    )
    await hass.async_block_till_done()

    assert len(cleared) == 1
    cleared_data = cleared[0].data
    assert cleared_data["issue_id"] == issue_id
    assert cleared_data["translation_key"] == "empty_signal_store"
    assert cleared_data["entry_id"] == mock_config_entry.entry_id


async def test_delete_issue_is_quiet_when_nothing_existed(hass: HomeAssistant) -> None:
    """Deleting an issue that was never raised does not fire an event."""
    cleared = _catch(hass, events.EVENT_ISSUE_CLEARED)

    events.delete_issue(
        hass,
        issue_id="never_existed",
        entry_id="fake_entry",
        translation_key="some_key",
        severity=ir.IssueSeverity.WARNING,
    )
    await hass.async_block_till_done()

    assert cleared == []


async def test_create_issue_redacts_cloudhook_url_but_keeps_other_urls(
    hass: HomeAssistant,
) -> None:
    """A cloudhook URL is reduced to its host; a plain docs URL is untouched."""
    raised = _catch(hass, events.EVENT_ISSUE_RAISED)

    events.create_issue(
        hass,
        issue_id="redaction_test",
        entry_id="fake_entry",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="no_matching_webhook",
        translation_placeholders={
            "callback_url": "https://abc123.ui.hooks.nabu.casa/secret-token-path",
            "docs_url": "https://github.com/example/repo#section",
        },
    )
    await hass.async_block_till_done()

    assert len(raised) == 1
    placeholders = raised[0].data["placeholders"]
    assert placeholders["callback_url"] == "abc123.ui.hooks.nabu.casa"
    assert "secret-token-path" not in placeholders["callback_url"]
    assert placeholders["docs_url"] == "https://github.com/example/repo#section"


async def test_create_issue_with_no_placeholders(hass: HomeAssistant) -> None:
    """A repair issue with no placeholders still fires a valid event."""
    raised = _catch(hass, events.EVENT_ISSUE_RAISED)

    events.create_issue(
        hass,
        issue_id="no_placeholders_test",
        entry_id="fake_entry",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="some_key",
    )
    await hass.async_block_till_done()

    assert len(raised) == 1
    assert raised[0].data["placeholders"] == {}
