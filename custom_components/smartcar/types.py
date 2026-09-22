"""Smartcar dataclasses and typing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

if TYPE_CHECKING:
    from .auth import AbstractAuth
    from .budget import ApiBudget
    from .coordinator import SmartcarVehicleCoordinator
    from .management import ManagementApi


type APIVersion = Literal["v2", "v3"]


@dataclass(frozen=True, kw_only=True)
class SmartcarData:
    """The Smartcar coordinator runtime data."""

    auth: AbstractAuth
    coordinators: dict[str, SmartcarVehicleCoordinator]
    meta_coordinator: DataUpdateCoordinator
    budget: ApiBudget
    management: ManagementApi


# the quality scale's strict-typing rule requires a typed config entry alias
# wherever runtime_data is used, so that entry.runtime_data is not Any.
type SmartcarConfigEntry = ConfigEntry[SmartcarData]


@dataclass
class SmartcarAPIError(Exception):
    """Error representing an issue via the Smartcar API."""

    code: int
    reason: str
    error_type: str | None = None
    error_code: str | None = None
    resolution_type: str | None = None
    suggested_user_message: str | None = None
