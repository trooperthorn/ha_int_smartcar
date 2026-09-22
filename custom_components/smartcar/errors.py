from collections.abc import Sequence

from homeassistant.exceptions import HomeAssistantError


class EmptyVehicleListError(HomeAssistantError):
    """Error to indicate no vehicles were returned by the API."""


class UnsupportedUserConfigurationError(HomeAssistantError):
    """Error to indicate multiple users are linked to a Smartcar application."""


class InvalidAuthError(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class MissingRequiredPermissionsError(HomeAssistantError):
    """Error to indicate a permission the integration cannot work without."""

    def __init__(self, missing: Sequence[str]) -> None:
        """Initialize with the permissions Smartcar did not grant."""
        super().__init__(f"missing required permissions: {list(missing)}")
        self.missing = list(missing)
