from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
from typing import Any

from aiohttp import ClientResponseError, RequestInfo
from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AUTH_CALLBACK_PATH,
    MY_AUTH_CALLBACK_PATH,
    AbstractOAuth2Implementation,
)
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN, OAUTH2_TOKEN_LEGACY
from .util import api_version_for_client_id

_LOGGER = logging.getLogger(__name__)


@dataclass
class SmartcarAuthorizationServer(AuthorizationServer):
    """Represent Smartcar OAuth2 Authorization Server(s)."""

    token_url_v2: str


class SmartcarAuthImplementation(AuthImplementation):
    """Smartcar local OAuth2 implementation."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth_domain: str,
        credential: ClientCredential,
        authorization_server: AuthorizationServer,
    ) -> None:
        """Initialize AuthImplementation."""
        super().__init__(
            hass,
            auth_domain,
            credential,
            authorization_server,
        )

        if api_version_for_client_id(credential.client_id) == "v2":
            assert isinstance(authorization_server, SmartcarAuthorizationServer)
            self.token_url = authorization_server.token_url_v2

    async def _token_request(self, data: dict) -> dict:
        """Request a token.

        For v3 this ignores the grant in `data` entirely. Smartcar's v3 API is
        machine to machine: `iam.smartcar.com/oauth2/token` accepts only
        `client_credentials`, there is no authorization code to exchange and no
        refresh token to present, so both the initial request and every refresh
        are the same client credentials call.

        A rejected credential or an unusable response is reported as one of the
        core OAuth2 token errors, so the config flow aborts with a reason the
        user can act on instead of raising an unhandled exception.

        Returns:
            The token response, with `refresh_token` set to None so that the
            core refresh path has the key it expects.
        """
        if api_version_for_client_id(self.client_id) == "v2":
            return await super()._token_request(data)

        session = async_get_clientsession(self.hass)
        response = await session.post(
            self.token_url,
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        )

        body = await response.text()

        try:
            response.raise_for_status()
        except ClientResponseError as err:
            raise self._token_error(err, body) from err

        return self._validated_token(body)

    def _request_info(self) -> RequestInfo:
        """Describe the token request for an error raised off the success path.

        The core OAuth2 errors are ClientResponseError subclasses and need a
        RequestInfo. Building it from what this implementation already knows
        avoids depending on an attribute of the response object, which is not
        guaranteed to be present on every client.

        Returns:
            Request info describing the token call.
        """
        url = URL(self.token_url)

        return RequestInfo(
            url=url,
            method="POST",
            headers=CIMultiDictProxy(CIMultiDict()),
            real_url=url,
        )

    def _token_error(
        self, err: ClientResponseError, body: str
    ) -> OAuth2TokenRequestError:
        """Translate a token endpoint failure into the error core expects.

        Smartcar answers `400 invalid_request` for a malformed call and
        `401 invalid_client` for credentials it does not recognise. Both mean
        the configured application credentials cannot be used, so both need to
        reach the user as a reauth rather than as a retry.

        Returns:
            The error to raise.
        """
        detail = _describe_oauth_error(body) or err.message

        _LOGGER.debug(
            "Smartcar rejected the v3 token request with %s: %s",
            err.status,
            detail,
        )

        error_class = (
            OAuth2TokenRequestTransientError
            if err.status == HTTPStatus.TOO_MANY_REQUESTS or err.status >= 500
            else OAuth2TokenRequestReauthError
        )

        return error_class(
            request_info=err.request_info,
            history=err.history,
            status=err.status,
            message=detail,
            headers=err.headers,
            domain=self.domain,
        )

    def _validated_token(self, body: str) -> dict:
        """Parse and sanity check a 2xx token response.

        A 2xx that does not carry a usable bearer token is a configuration
        problem, not a transient one, so it is reported as such instead of
        failing later on the first API call with a less obvious error.

        Returns:
            The token response.
        """
        try:
            token: Any = json.loads(body)
        except ValueError as err:
            reason = "the response is not JSON"
            raise self._unusable_token(reason) from err

        if not isinstance(token, dict):
            reason = "the response is not an object"
            raise self._unusable_token(reason)

        kind = str(token.get("token_type", "")).lower()

        if kind != "bearer":
            reason = f"unusable token type {kind or 'none'}"
            raise self._unusable_token(reason)

        if not token.get("access_token"):
            reason = "no access token"
            raise self._unusable_token(reason)

        return {"refresh_token": None, **token}

    def _unusable_token(self, reason: str) -> OAuth2TokenRequestError:
        """Build the error for a 2xx that did not carry a usable token.

        Returns:
            The error to raise.
        """
        return OAuth2TokenRequestError(
            request_info=self._request_info(),
            status=HTTPStatus.OK,
            message=f"Smartcar returned a token response that cannot be used: {reason}",
            domain=self.domain,
        )


def _describe_oauth_error(body: str) -> str | None:
    """Pull `error` and `error_description` out of a token error body.

    Returns:
        A readable description, or None when the body is not the documented
        shape.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:200] or None

    if not isinstance(parsed, dict) or not (error := parsed.get("error")):
        return body[:200] or None

    if description := parsed.get("error_description"):
        return f"{error}: {description}"

    return str(error)


async def async_get_auth_implementation(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
) -> AbstractOAuth2Implementation:
    return SmartcarAuthImplementation(
        hass,
        auth_domain,
        credential,
        authorization_server=await async_get_authorization_server(hass),
    )


async def async_get_authorization_server(  # noqa: RUF029
    hass: HomeAssistant,  # noqa: ARG001
) -> AuthorizationServer:
    """Return authorization server details."""
    return SmartcarAuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=OAUTH2_TOKEN,
        token_url_v2=OAUTH2_TOKEN_LEGACY,
    )


async def async_get_description_placeholders(  # noqa: RUF029
    hass: HomeAssistant,
) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    if "my" in hass.config.components:
        redirect_url = MY_AUTH_CALLBACK_PATH
    else:
        ha_host = hass.config.external_url or "https://YOUR_DOMAIN:PORT"
        redirect_url = f"{ha_host}{AUTH_CALLBACK_PATH}"
    return {
        "more_info_url": "https://github.com/wbyoung/smartcar?tab=readme-ov-file#configuration",
        "oauth_creds_url": "https://dashboard.smartcar.com/team/applications",
        "redirect_url": redirect_url,
    }
