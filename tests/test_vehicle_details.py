"""Tests for identifying a vehicle at setup without depending on its signals.

A live account showed that a vehicle can have an empty signal store: every
signal path answers `404 SIGNAL_NOT_FOUND` while the connection is healthy and
the vehicle endpoint answers normally. Setup used to read the VIN signal and
call `raise_for_status`, so that vehicle could not be set up at all.

See docs/api-reference.md, "Vehicle data", for the responses these pin.
"""

from http import HTTPStatus
from typing import Any

from aiohttp import ClientResponseError, RequestInfo
import pytest
from yarl import URL

from custom_components.smartcar import (
    _fetch_vin,  # noqa: PLC2701
    _store_vehicle_details,  # noqa: PLC2701
)

VEHICLE_ID = "8e5a8dca-a62d-466b-96f7-d204ff1787a8"
CONNECTION_DETAILS = {
    "make": "VOLKSWAGEN",
    "model": "ID. Buzz",
    "year": 2025,
    "powertrainType": "BEV",
}


class _Response:
    """A response that behaves like aiohttp's, including a real status."""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        """Raise for a 4xx or 5xx, as aiohttp does.

        Raises:
            ClientResponseError: When the status is an error.
        """
        if self.status >= HTTPStatus.BAD_REQUEST:
            raise ClientResponseError(
                RequestInfo(URL("https://example.invalid"), "GET", None, None),  # type: ignore[arg-type]
                (),
                status=self.status,
            )

    async def json(self) -> dict:
        """Return the body.

        Returns:
            The body.
        """
        return self._body


class _Auth:
    """An auth that answers a scripted response per path."""

    version = "v3"

    def __init__(self, responses: dict[str, _Response]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    async def request_v3(self, method: str, path: str, **kwargs: Any) -> _Response:  # noqa: ARG002
        """Answer the scripted response.

        Returns:
            The response for this path.

        Raises:
            AssertionError: If the path was not scripted, which means the code
                made a request the test did not expect.
        """
        self.requested.append(path)

        if path not in self._responses:
            message = f"unexpected request: {path}"
            raise AssertionError(message)

        return self._responses[path]


def _vin_body(*, included: bool = True) -> dict:
    body: dict[str, Any] = {
        "data": {"attributes": {"body": {"value": "WVWZZZ1KZAW123456"}}}
    }

    if included:
        body["included"] = {"vehicle": {"attributes": CONNECTION_DETAILS}}

    return body


async def test_a_missing_vin_signal_does_not_stop_setup() -> None:
    """A vehicle with no stored signals is still set up, without a VIN.

    This is the live case: the store is empty, so the VIN signal 404s. The VIN
    is only used to notice the same car configured twice, which is worth having
    and worth doing without.
    """
    auth = _Auth(
        {
            f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin": _Response(
                HTTPStatus.NOT_FOUND
            )
        }
    )
    data: dict[str, Any] = {"vehicles": {}}

    await _store_vehicle_details(data, auth, VEHICLE_ID, CONNECTION_DETAILS)  # type: ignore[arg-type]

    assert data["vehicles"][VEHICLE_ID] == {
        "make": "VOLKSWAGEN",
        "model": "ID. Buzz",
        "year": "2025",
    }
    assert "vin" not in data["vehicles"][VEHICLE_ID]


async def test_the_connection_describes_the_vehicle() -> None:
    """What the connection said is used, rather than the signal response.

    The connection carries make, model and year already, and it was read for
    free. Preferring it means the identity of the vehicle does not depend on
    the signal store holding anything.
    """
    auth = _Auth(
        {
            f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin": _Response(
                HTTPStatus.OK,
                {
                    **_vin_body(included=False),
                    "included": {"vehicle": {"attributes": {"make": "WRONG"}}},
                },
            )
        }
    )
    data: dict[str, Any] = {"vehicles": {}}

    await _store_vehicle_details(data, auth, VEHICLE_ID, CONNECTION_DETAILS)  # type: ignore[arg-type]

    assert data["vehicles"][VEHICLE_ID]["make"] == "VOLKSWAGEN"
    assert data["vehicles"][VEHICLE_ID]["vin"] == "WVWZZZ1KZAW123456"


async def test_the_signal_response_is_the_first_fallback() -> None:
    """With no connection attributes, what came back with the VIN is used."""
    auth = _Auth(
        {
            f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin": _Response(
                HTTPStatus.OK, _vin_body()
            )
        }
    )
    data: dict[str, Any] = {"vehicles": {}}

    await _store_vehicle_details(data, auth, VEHICLE_ID)  # type: ignore[arg-type]

    assert data["vehicles"][VEHICLE_ID]["model"] == "ID. Buzz"
    assert auth.requested == [
        f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin"
    ]


async def test_the_vehicle_endpoint_is_the_last_resort() -> None:
    """Nothing else described the vehicle, so ask the endpoint that always can.

    The vehicle endpoint answers for a vehicle with nothing collected yet,
    which is exactly the case the signal endpoints cannot serve.
    """
    auth = _Auth(
        {
            f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin": _Response(
                HTTPStatus.NOT_FOUND
            ),
            f"vehicles/{VEHICLE_ID}": _Response(
                HTTPStatus.OK, {"data": {"attributes": CONNECTION_DETAILS}}
            ),
        }
    )
    data: dict[str, Any] = {"vehicles": {}}

    await _store_vehicle_details(data, auth, VEHICLE_ID)  # type: ignore[arg-type]

    assert data["vehicles"][VEHICLE_ID]["year"] == "2025"
    assert auth.requested[-1] == f"vehicles/{VEHICLE_ID}"


async def test_a_failure_that_is_not_a_missing_signal_still_raises() -> None:
    """Only 404 is tolerated. A 500 is a real failure and must surface."""
    auth = _Auth(
        {
            f"vehicles/{VEHICLE_ID}/signals/vehicleidentification-vin": _Response(
                HTTPStatus.INTERNAL_SERVER_ERROR
            )
        }
    )

    with pytest.raises(ClientResponseError):
        await _fetch_vin(auth, VEHICLE_ID)  # type: ignore[arg-type]
