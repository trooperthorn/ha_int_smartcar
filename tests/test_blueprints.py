"""Every shipped blueprint is valid and refers to things that exist.

A blueprint that does not load is worse than no blueprint: the user finds out
when they try to import it. These tests load each one through Home Assistant's
own blueprint schema, so a typo in a selector or a missing input fails here
rather than in someone's config.
"""

from pathlib import Path
from typing import Any

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.util.yaml import parse_yaml
import pytest

BLUEPRINT_ROOT = Path(__file__).parent.parent / "blueprints"

BLUEPRINTS = sorted(BLUEPRINT_ROOT.rglob("*.yaml"))

# actions this integration provides, used to catch a blueprint calling an
# action that was renamed or never existed.
SMARTCAR_ACTIONS = {
    "smartcar.refresh_vehicle",
    "smartcar.lock_doors",
    "smartcar.unlock_doors",
}


def _schema(domain: str) -> Any:
    """The blueprint schema Home Assistant validates this domain with.

    Returns:
        The voluptuous schema.
    """
    return AUTOMATION_BLUEPRINT_SCHEMA if domain == "automation" else BLUEPRINT_SCHEMA


def _blueprint(path: Path) -> Blueprint:
    """Build a Blueprint the way Home Assistant's import dialog does.

    Returns:
        The validated blueprint.
    """
    domain = path.parent.parent.name

    return Blueprint(
        _load(path),
        expected_domain=domain,
        path=str(path),
        schema=_schema(domain),
    )


def _load(path: Path) -> dict[str, Any]:
    """Parse a blueprint with the loader that understands `!input`.

    Returns:
        The parsed document.
    """
    parsed: dict[str, Any] = parse_yaml(path.read_text(encoding="utf-8"))
    return parsed


def test_blueprints_are_shipped() -> None:
    """The blueprint directory is not silently empty."""
    assert BLUEPRINTS, "no blueprints found to validate"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_blueprint_is_valid(path: Path) -> None:
    """Each blueprint passes Home Assistant's schema.

    This is the same validation the import dialog runs, so a blueprint that
    passes here will import.
    """
    blueprint = _blueprint(path)

    assert blueprint.name
    assert blueprint.metadata["description"].strip()


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_blueprint_inputs_are_all_used(path: Path) -> None:
    """An input nothing consumes is a leftover, and confuses the user.

    The import dialog shows every declared input, so one that no action reads
    is a question asked for no reason.
    """
    raw = path.read_text(encoding="utf-8")
    blueprint = _blueprint(path)

    for name in blueprint.inputs:
        assert f"!input {name}" in raw, f"input {name!r} is declared but never used"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_blueprint_only_calls_actions_that_exist(path: Path) -> None:
    """A blueprint calling a renamed Smartcar action would fail at runtime."""
    raw = path.read_text(encoding="utf-8")

    for line in raw.splitlines():
        stripped = line.strip()

        if not stripped.startswith("- action: smartcar."):
            continue

        called = stripped.removeprefix("- action: ").strip()

        assert called in SMARTCAR_ACTIONS, f"{path.name} calls unknown action {called}"
