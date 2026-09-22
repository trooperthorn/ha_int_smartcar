"""Tests for the Management API webhook health evaluation."""

from custom_components.smartcar.management import webhook_health


def test_webhook_health_reports_disabled() -> None:
    """A disabled webhook can never collect anything, whatever else is set."""
    health = webhook_health(
        [
            {
                "id": "hook_1",
                "attributes": {
                    "isEnabled": False,
                    "triggers": ["a"],
                    "data": ["a"],
                },
            }
        ],
        "hook_1",
    )

    assert health is not None
    assert health.is_enabled is False
    assert health.trigger_count == 1
    assert health.data_count == 1
    assert health.is_healthy is False


def test_webhook_health_reports_empty_lists() -> None:
    """Sean's exact broken case: enabled, but nothing configured to collect."""
    health = webhook_health(
        [
            {
                "id": "hook_1",
                "attributes": {"isEnabled": True, "triggers": [], "data": []},
            }
        ],
        "hook_1",
    )

    assert health is not None
    assert health.is_healthy is False
    assert health.trigger_count == 0
    assert health.data_count == 0


def test_webhook_health_healthy() -> None:
    """Enabled, with at least one trigger and one data signal, is healthy."""
    health = webhook_health(
        [
            {
                "id": "hook_1",
                "attributes": {
                    "isEnabled": True,
                    "triggers": ["closure-islocked"],
                    "data": ["closure-islocked", "tractionbattery-stateofcharge"],
                },
            }
        ],
        "hook_1",
    )

    assert health is not None
    assert health.is_healthy is True


def test_webhook_health_no_match() -> None:
    """The target webhook id may have been deleted between list and check."""
    assert webhook_health([{"id": "hook_1", "attributes": {}}], "hook_2") is None


def test_webhook_health_non_dict_attributes() -> None:
    """A malformed attributes value must not crash the health check."""
    health = webhook_health([{"id": "hook_1", "attributes": "nope"}], "hook_1")

    assert health is not None
    assert health.is_enabled is False
    assert health.trigger_count == 0
    assert health.data_count == 0
