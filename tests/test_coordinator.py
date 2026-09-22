"""Test signal timestamp handling across polling and webhooks."""

import copy
import datetime as dt
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartcar import (
    coordinator as coordinator_module,
    webhooks as webhooks_module,
)
from custom_components.smartcar.const import DOMAIN

from . import setup_integration


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_v3_polling_preserves_signal_metadata(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """Expose per-signal OEM and retrieval times from the v3 response."""
    original_response = copy.deepcopy(vehicle["_api"])
    await setup_integration(hass, mock_config_entry)
    assert vehicle["_api"] == original_response
    state = hass.states.get("sensor.vw_id_4_battery")
    assert state is not None
    assert state.attributes["age"] == "2026-06-29T23:55:53+00:00"
    assert state.attributes["fetched_at"] == "2026-06-30T00:03:53.743000+00:00"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (
            "2026-06-29T23:55:53.000Z",
            dt.datetime(2026, 6, 29, 23, 55, 53, tzinfo=dt.UTC),
        ),
        (
            "2026-06-30T01:55:53+02:00",
            dt.datetime(2026, 6, 29, 23, 55, 53, tzinfo=dt.UTC),
        ),
        (1782777353000, dt.datetime(2026, 6, 29, 23, 55, 53, tzinfo=dt.UTC)),
        (1782777353743.0, dt.datetime(2026, 6, 29, 23, 55, 53, 743000, tzinfo=dt.UTC)),
        (0, None),
        (None, None),
        ("not-a-timestamp", None),
    ],
    ids=[
        "iso-utc",
        "iso-offset",
        "milliseconds",
        "fractional-milliseconds",
        "zero-unavailable",
        "missing",
        "invalid",
    ],
)
def test_signal_timestamps(timestamp, expected: dt.datetime | None) -> None:
    """Parse both API timestamp formats without changing the payload."""
    signal = {
        "code": "tractionbattery-stateofcharge",
        "body": {"value": 73, "unit": "percent"},
        "meta": {"oemUpdatedAt": timestamp, "retrievedAt": timestamp},
    }
    original = copy.deepcopy(signal)
    old_time = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    data: dict = {
        "tractionbattery-stateofcharge:data_age": old_time,
        "tractionbattery-stateofcharge:fetched_at": old_time,
    }
    coordinator_module._DataAdder(data).from_signal_attributes(signal)

    assert data["tractionbattery-stateofcharge"] == {"value": 0.73}
    assert data.get("tractionbattery-stateofcharge:data_age") == expected
    assert data.get("tractionbattery-stateofcharge:fetched_at") == expected
    assert signal == original


def test_failed_signal_preserves_previous_timestamps() -> None:
    """A failed signal must not replace the last successful observation time."""
    old_time = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    data: dict = {
        "tractionbattery-stateofcharge": {"value": 0.73},
        "tractionbattery-stateofcharge:data_age": old_time,
        "tractionbattery-stateofcharge:fetched_at": old_time,
    }
    coordinator_module._DataAdder(data).from_signal_attributes(
        {
            "code": "tractionbattery-stateofcharge",
            "status": {"value": "ERROR"},
            "meta": {
                "oemUpdatedAt": "2026-06-29T23:55:53.000Z",
                "retrievedAt": "2026-06-30T00:03:53.743Z",
            },
        }
    )
    assert data["tractionbattery-stateofcharge"] == {"value": None}
    assert data["tractionbattery-stateofcharge:data_age"] == old_time
    assert data["tractionbattery-stateofcharge:fetched_at"] == old_time


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_access_token_is_redacted_from_request_logs(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The access token is an application-level bearer credential.

    Debug logs get pasted into issue reports, so it must never appear there,
    the same way the webhook management token is kept out.
    """
    with caplog.at_level(logging.DEBUG, logger="custom_components.smartcar.auth"):
        await setup_integration(hass, mock_config_entry)

    request_lines = [line for line in caplog.text.splitlines() if "HTTP " in line]
    assert request_lines
    assert all("<redacted>" in line for line in request_lines)
    assert "Bearer " not in caplog.text


@pytest.mark.parametrize(
    ("code", "alternate_key"),
    [("closure-doors", "doors"), ("closure-windows", "windows")],
)
def test_alternate_body_values_key_is_accepted(code: str, alternate_key: str) -> None:
    """The OpenAPI spec's own example body uses `doors`/`windows`, not `values`.

    Entity descriptions read `closure-doors.values` / `closure-windows.values`
    unconditionally, so a body shaped like the spec example rather than the
    `values` shape every live payload actually uses must be normalized, not
    silently ignored.
    """
    data: dict = {}
    items = [{"row": 0, "column": 0, "isOpen": False, "isLocked": True}]
    coordinator_module._DataAdder(data).from_signal_attributes(
        {
            "code": code,
            "body": {alternate_key: items, "rowCount": 1, "columnCount": 1},
            "meta": {},
        }
    )

    assert data[code] == {"values": items, "rowCount": 1, "columnCount": 1}


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_empty_signal_store_raises_and_clears_a_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """An empty store is a distinct, named problem from every entity's own state.

    v3 answering `200` with `data: []` is otherwise indistinguishable from a
    slow OEM, and it is exactly what Sean's disabled webhook produced: every
    entity created, none of them ever getting a value, nothing in the logs.
    """
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    issue_registry = ir.async_get(hass)
    issue_id = (
        f"empty_signal_store_{mock_config_entry.entry_id}_{coordinator.vehicle_id}"
    )

    coordinator._merge_signal_data({"data": [], "meta": {"totalCount": 0}})

    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["title"] == mock_config_entry.title

    coordinator._merge_signal_data(
        {
            "data": [
                {
                    "attributes": {
                        "code": "closure-islocked",
                        "body": {"value": True},
                        "meta": {},
                    }
                }
            ],
            "meta": {"totalCount": 1},
        }
    )

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_webhook_signals_clear_the_empty_store_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A webhook delivery with data is proof the store is no longer empty."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    issue_registry = ir.async_get(hass)
    issue_id = (
        f"empty_signal_store_{mock_config_entry.entry_id}_{coordinator.vehicle_id}"
    )

    coordinator._update_empty_store_issue(has_signals=False)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    webhooks_module._handle_webhook_signals(
        coordinator,
        [{"code": "closure-islocked", "body": {"value": True}, "meta": {}}],
    )

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_one_malformed_signal_does_not_drop_the_rest_of_the_event(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A signal shaped nothing like a signal must not abort the whole delivery."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    webhooks_module._handle_webhook_signals(
        coordinator,
        [
            None,  # type: ignore[list-item]
            {
                "code": "tractionbattery-stateofcharge",
                "body": {"value": 55, "unit": "percent"},
                "meta": {},
            },
        ],
    )

    assert "Ignoring malformed signal" in caplog.text
    assert coordinator.data["tractionbattery-stateofcharge"] == {"value": 0.55}


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_first_live_webhook_is_only_logged_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The second live delivery must not repeat the "first" log line."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    coordinator.note_live_webhook()
    caplog.clear()
    coordinator.note_live_webhook()

    assert "first live webhook delivery received" not in caplog.text


@pytest.mark.parametrize("vehicle_fixture", ["vw_id_4"])
@pytest.mark.parametrize("client_id_version", ["v3"])
async def test_signal_with_no_code_is_skipped_in_poll_summary(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    vehicle: dict,
) -> None:
    """A signal missing `code` must not crash the poll summary logger."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    coordinator._merge_signal_data(
        {
            "data": [
                {"attributes": {}},
                {"attributes": {"code": "closure-islocked", "body": {"value": True}}},
            ],
            "meta": {"totalCount": 2},
        }
    )

    assert coordinator.last_poll_unmapped_codes == []
