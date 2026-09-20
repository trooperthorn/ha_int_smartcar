"""The Smartcar Management API, used to subscribe vehicles to a webhook.

A webhook is the only way to get data more often than once a day, and the only
way to get it without spending the vehicle's monthly call allowance. Until now
the user had to create the subscription by hand in the Smartcar dashboard for
every vehicle, which is easy to forget and impossible to verify from here.

`https://management.api.smartcar.com/v3` carries the endpoints to do it, and
takes the same application-level bearer token as the Vehicle API. Note that the
prose reference still describes an older host with a separate management token;
`management.yaml` is the newer of the two and is what this follows.

Subscription calls are not addressed to a vehicle in the billing sense and do
not come out of its allowance. They are, however, subject to the ordinary
application rate limit, so they happen at setup and teardown rather than on any
schedule.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .auth import AbstractAuth

_LOGGER = logging.getLogger(__name__)

MANAGEMENT_ENDPOINT = "https://management.api.smartcar.com/v3"


class ManagementApi:
    """Read and write webhook subscriptions for the application."""

    def __init__(self, auth: AbstractAuth, websession: ClientSession) -> None:
        """Initialize the client."""
        self._auth = auth
        self._websession = websession

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Make an authenticated Management API request.

        A non 2xx status raises, and every caller catches: a Management API
        failure is never allowed to take the integration down with it.

        Returns:
            The decoded body, or None for an empty one.
        """
        token = await self._auth.async_get_access_token()

        response = await self._websession.request(
            method,
            f"{MANAGEMENT_ENDPOINT}/{path}",
            headers={"authorization": f"Bearer {token}"},
            **kwargs,
        )
        response.raise_for_status()

        if response.status == HTTPStatus.NO_CONTENT:
            return None

        return await response.json(content_type=None)

    async def async_list_webhooks(self) -> list[dict[str, Any]]:
        """Every webhook configured on the application.

        Returns:
            The webhook resources, or an empty list if none could be read.
        """
        try:
            body = await self._request("get", "webhooks")
        except (ClientResponseError, ClientError, ValueError):
            _LOGGER.warning("Could not list Smartcar webhooks", exc_info=True)
            return []

        if not isinstance(body, dict):
            return []

        return [item for item in body.get("data", []) if isinstance(item, dict)]

    async def async_subscriptions_for_vehicle(
        self, vehicle_id: str
    ) -> list[dict[str, Any]]:
        """Subscriptions that already exist for one vehicle.

        Returns:
            The subscription resources, or an empty list if none could be read.
        """
        try:
            body = await self._request(
                "get", "subscriptions", params={"filter[vehicleId]": vehicle_id}
            )
        except (ClientResponseError, ClientError, ValueError):
            _LOGGER.warning(
                "Could not list subscriptions for vehicle %s", vehicle_id, exc_info=True
            )
            return []

        if not isinstance(body, dict):
            return []

        return [item for item in body.get("data", []) if isinstance(item, dict)]

    async def async_subscribe(
        self, *, webhook_id: str, user_id: str, vehicle_id: str
    ) -> bool:
        """Subscribe one vehicle to one webhook.

        The endpoint answers `202 Accepted` because the subscription is created
        asynchronously, and `409 Conflict` when it already exists. A conflict is
        success from the caller's point of view: the vehicle ends up subscribed
        either way.

        Returns:
            True when the vehicle is subscribed.
        """
        try:
            await self._request(
                "post",
                "subscriptions",
                json={
                    "data": {
                        "attributes": {
                            "webhookId": webhook_id,
                            "userId": user_id,
                            "vehicleId": vehicle_id,
                        }
                    }
                },
            )
        except ClientResponseError as err:
            if err.status == HTTPStatus.CONFLICT:
                _LOGGER.debug(
                    "Vehicle %s is already subscribed to webhook %s",
                    vehicle_id,
                    webhook_id,
                )
                return True

            _LOGGER.warning(
                "Could not subscribe vehicle %s to webhook %s: %s",
                vehicle_id,
                webhook_id,
                err.message,
            )
            return False
        except (ClientError, ValueError):
            _LOGGER.warning(
                "Could not subscribe vehicle %s to webhook %s",
                vehicle_id,
                webhook_id,
                exc_info=True,
            )
            return False

        _LOGGER.info(
            "Subscribed vehicle %s to Smartcar webhook %s", vehicle_id, webhook_id
        )

        return True

    async def async_unsubscribe(self, subscription_id: str) -> bool:
        """Remove one subscription.

        Returns:
            True when the subscription is gone.
        """
        try:
            await self._request("delete", f"subscriptions/{subscription_id}")
        except ClientResponseError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                return True

            _LOGGER.warning(
                "Could not remove subscription %s: %s", subscription_id, err.message
            )
            return False
        except (ClientError, ValueError):
            _LOGGER.warning(
                "Could not remove subscription %s", subscription_id, exc_info=True
            )
            return False

        return True


def webhook_id_matching_url(
    webhooks: list[dict[str, Any]], callback_url: str
) -> str | None:
    """Find the webhook whose callback URL is this Home Assistant.

    The application may have several webhooks configured for other consumers.
    Subscribing a vehicle to the wrong one sends this instance nothing and the
    other consumer data it did not ask for, so the URL has to match rather than
    just the first webhook being used.

    Returns:
        The webhook id, or None when no webhook points here.
    """
    for webhook in webhooks:
        attributes = webhook.get("attributes", {})

        if not isinstance(attributes, dict):
            continue

        url = attributes.get("callbackUri") or attributes.get("url")

        if url and str(url).rstrip("/") == callback_url.rstrip("/"):
            return str(webhook.get("id")) if webhook.get("id") else None

    return None
