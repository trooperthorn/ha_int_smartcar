"""Test Smartcar diagnostics."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from custom_components.smartcar import diagnostics as diagnostics_module
from custom_components.smartcar.const import CONF_APPLICATION_MANAGEMENT_TOKEN
from custom_components.smartcar.webhooks import webhook_url_from_id

from . import setup_added_integration, setup_integration


@pytest.mark.usefixtures("enable_all_entities")
async def test_entry_diagnostics(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
    snapshot: SnapshotAssertion,
    vehicle: AsyncMock,
) -> None:
    """Test config entry diagnostics."""
    await setup_integration(hass, mock_config_entry)
    assert await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    ) == snapshot(
        exclude=props(
            "entry_id", "webhook_id", "created_at", "modified_at", "expires_at"
        )
    )


@pytest.mark.usefixtures("enable_all_entities")
@pytest.mark.parametrize(
    ("vehicle_fixture", "webhook_body", "webhook_status"),
    [
        ("jaguar_ipace", "all", 204),
        ("jaguar_ipace", "all", 401),
        ("vw_id_4", b"invalid_json", 204),
    ],
    indirect=["webhook_body"],
    ids=[
        "location_redaction",
        "signature_validation_failure",  # keeps raw response
        "invalid_json",
    ],
)
async def test_entry_diagnostics_metadata(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
    snapshot: SnapshotAssertion,
    vehicle_fixture: str,
    webhook_body: str | bytes,
    webhook_status: int,
) -> None:
    """Test config entry diagnostics."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: "test_amt",
        },
    )

    await setup_added_integration(hass, mock_config_entry)

    if isinstance(webhook_body, bytes):
        webhook_body = webhook_body.decode("utf-8")

    meta_coordinator = mock_config_entry.runtime_data.meta_coordinator
    meta_coordinator.async_set_updated_data(
        {
            "last_webhook_response": {
                "status": webhook_status,
            },
            "last_webhook_request": webhook_body,
        }
    )

    assert await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    ) == snapshot(
        exclude=props(
            "entry_id", "webhook_id", "created_at", "modified_at", "expires_at"
        )
    )


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
@pytest.mark.parametrize(
    ("webhooks", "expected"),
    [
        ([], {"matched": False}),
        (
            [{"id": "wh_1", "attributes": {"callbackUri": "REPLACE"}}],
            {"matched": True, "found": True, "isEnabled": False, "isHealthy": False},
        ),
        (
            [
                {
                    "id": "wh_1",
                    "attributes": {
                        "callbackUri": "REPLACE",
                        "isEnabled": True,
                        "triggers": ["closure-islocked"],
                        "data": ["closure-islocked"],
                    },
                }
            ],
            {"matched": True, "found": True, "isEnabled": True, "isHealthy": True},
        ),
    ],
    ids=["no_match", "matched_unhealthy", "matched_healthy"],
)
async def test_entry_diagnostics_webhook_health(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
    vehicle: AsyncMock,
    webhooks: list[dict],
    expected: dict,
) -> None:
    """The webhook health summary reflects what the Management API reports."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_diag_test",
            CONF_APPLICATION_MANAGEMENT_TOKEN: "test_amt",
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    callback_url, _ = await webhook_url_from_id(hass, "smartcar_diag_test")

    for webhook in webhooks:
        if webhook["attributes"].get("callbackUri") == "REPLACE":
            webhook["attributes"]["callbackUri"] = callback_url

    with patch.object(
        mock_config_entry.runtime_data.management,
        "async_list_webhooks",
        AsyncMock(return_value=webhooks),
    ):
        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, mock_config_entry
        )

    for key, value in expected.items():
        assert diagnostics["webhook_health"][key] == value


async def test_entry_diagnostics_webhook_health_failure_is_swallowed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
    vehicle: AsyncMock,
) -> None:
    """A Management API hiccup must not break the diagnostics download."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_diag_test_2",
            CONF_APPLICATION_MANAGEMENT_TOKEN: "test_amt",
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    with patch.object(
        mock_config_entry.runtime_data.management,
        "async_list_webhooks",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, mock_config_entry
        )

    assert diagnostics["webhook_health"] is None


async def test_webhook_health_summary_handles_a_vanished_webhook(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: AsyncMock,
) -> None:
    """The webhook can disappear between the URL match and the health read."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_WEBHOOK_ID: "smartcar_diag_test_3",
            CONF_APPLICATION_MANAGEMENT_TOKEN: "test_amt",
        },
    )
    await setup_added_integration(hass, mock_config_entry)

    with (
        patch.object(
            mock_config_entry.runtime_data.management,
            "async_list_webhooks",
            AsyncMock(return_value=[{"id": "wh_1", "attributes": {}}]),
        ),
        patch.object(
            diagnostics_module, "webhook_id_matching_url", return_value="wh_1"
        ),
        patch.object(diagnostics_module, "webhook_health", return_value=None),
    ):
        summary = await diagnostics_module._webhook_health_summary(
            mock_config_entry, "http://example/webhook"
        )

    assert summary == {"matched": True, "found": False}
