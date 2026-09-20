"""Test application_credentials."""

from typing import Any

from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartcar.application_credentials import (
    SmartcarAuthImplementation,
    async_get_auth_implementation,
    async_get_description_placeholders,
)
from custom_components.smartcar.const import DOMAIN, OAUTH2_TOKEN, OAUTH2_TOKEN_LEGACY


@pytest.mark.parametrize(
    ("additional_components", "external_url", "expected_redirect_uri"),
    [
        ([], "https://example.com", "https://example.com/auth/external/callback"),
        ([], None, "https://YOUR_DOMAIN:PORT/auth/external/callback"),
        (["my"], "https://example.com", "https://my.home-assistant.io/redirect/oauth"),
    ],
)
async def test_description_placeholders(
    hass: HomeAssistant,
    additional_components: list[str],
    external_url: str | None,
    expected_redirect_uri: str,
) -> None:
    """Test description placeholders."""
    hass.config.components.update(additional_components)
    hass.config.external_url = external_url
    placeholders = await async_get_description_placeholders(hass)
    assert placeholders == {
        "more_info_url": "https://github.com/wbyoung/smartcar?tab=readme-ov-file#configuration",
        "oauth_creds_url": "https://dashboard.smartcar.com/team/applications",
        "redirect_url": expected_redirect_uri,
    }


@pytest.fixture
async def implementation(hass: HomeAssistant) -> SmartcarAuthImplementation:
    """A v3 implementation, i.e. one whose client id has the client_ prefix."""
    implementation = await async_get_auth_implementation(
        hass, DOMAIN, ClientCredential("client_mock-id", "mock-secret")
    )
    assert isinstance(implementation, SmartcarAuthImplementation)
    return implementation


async def test_v3_token_request_uses_client_credentials(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    implementation: SmartcarAuthImplementation,
) -> None:
    """v3 ignores the grant it is handed and asks for an application token.

    There is no authorization code to exchange and no refresh token to present,
    so the initial request and every later refresh are the same call.
    """
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={"access_token": "mock-token", "token_type": "Bearer", "expires_in": 3600},
    )

    token = await implementation._token_request({"grant_type": "refresh_token"})

    assert token == {
        "refresh_token": None,
        "access_token": "mock-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    (_method, _url, data, _headers) = aioclient_mock.mock_calls[0]
    assert data == {
        "client_id": "client_mock-id",
        "client_secret": "mock-secret",
        "grant_type": "client_credentials",
    }


@pytest.mark.parametrize(
    ("status", "body", "expected_error", "expected_message"),
    [
        (
            401,
            {"error": "invalid_client"},
            OAuth2TokenRequestReauthError,
            "invalid_client",
        ),
        (
            400,
            {"error": "invalid_request", "error_description": "missing grant_type"},
            OAuth2TokenRequestReauthError,
            "invalid_request: missing grant_type",
        ),
        (401, "not json at all", OAuth2TokenRequestReauthError, "not json at all"),
        (
            401,
            {"message": "no error key here"},
            OAuth2TokenRequestReauthError,
            "no error key here",
        ),
        (429, {"error": "slow_down"}, OAuth2TokenRequestTransientError, "slow_down"),
        (
            503,
            {"error": "unavailable"},
            OAuth2TokenRequestTransientError,
            "unavailable",
        ),
    ],
    ids=[
        "invalid_client",
        "invalid_request",
        "unreadable",
        "unexpected_shape",
        "rate_limited",
        "outage",
    ],
)
async def test_v3_token_request_reports_rejections(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    implementation: SmartcarAuthImplementation,
    status: int,
    body: dict | str,
    expected_error: type[Exception],
    expected_message: str,
) -> None:
    """A rejected credential reports as a reauth, an outage as transient.

    This is what makes a mistyped or wrong-type client id say so. Before, any
    non-2xx fell through to an assertion, which the config flow does not catch,
    so the user saw an unknown error and no way to act on it.
    """
    kwargs: dict[str, Any] = (
        {"json": body} if isinstance(body, dict) else {"text": body}
    )
    aioclient_mock.post(OAUTH2_TOKEN, status=status, **kwargs)

    with pytest.raises(expected_error) as excinfo:
        await implementation._token_request({"grant_type": "client_credentials"})

    assert expected_message in str(excinfo.value.message)


@pytest.mark.parametrize(
    ("body", "expected_message"),
    [
        ("[]", "not an object"),
        ("{ not json", "not JSON"),
        ('{"access_token": "t", "token_type": "mac"}', "unusable token type mac"),
        ('{"token_type": "Bearer"}', "no access token"),
    ],
    ids=["not_an_object", "not_json", "wrong_token_type", "no_access_token"],
)
async def test_v3_token_request_rejects_unusable_success(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    implementation: SmartcarAuthImplementation,
    body: str,
    expected_message: str,
) -> None:
    """A 200 that is not a usable bearer token fails now, not on the first call."""
    aioclient_mock.post(OAUTH2_TOKEN, text=body)

    with pytest.raises(OAuth2TokenRequestError) as excinfo:
        await implementation._token_request({"grant_type": "client_credentials"})

    assert expected_message in str(excinfo.value.message)


async def test_v2_token_request_is_left_to_core(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A legacy client id keeps the ordinary authorization code exchange."""
    implementation = await async_get_auth_implementation(
        hass, DOMAIN, ClientCredential("legacy-mock-id", "mock-secret")
    )

    assert implementation.token_url == OAUTH2_TOKEN_LEGACY

    aioclient_mock.post(
        OAUTH2_TOKEN_LEGACY,
        json={"access_token": "mock-token", "token_type": "Bearer", "expires_in": 3600},
    )

    token = await implementation._token_request(
        {"grant_type": "authorization_code", "code": "mock-code"}
    )

    assert token["access_token"] == "mock-token"  # noqa: S105
    assert "refresh_token" not in token

    (_method, _url, data, _headers) = aioclient_mock.mock_calls[0]
    assert data["grant_type"] == "authorization_code"
