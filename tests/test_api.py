"""Tests for the asynchronous Eliq client and token rotation."""

from unittest.mock import AsyncMock

import pytest

from custom_components.hehku_energy.api import (
    Credentials,
    HehkuApi,
    HehkuAuthError,
    HehkuClient,
    parse_token_response,
)


def credentials(access: str = "old-access", refresh: str = "old-refresh") -> Credentials:
    return Credentials(user_id="7", access_token=access, refresh_token=refresh, device_uuid="uuid")


def test_refresh_response_preserves_unrotated_refresh_token() -> None:
    result = parse_token_response(
        {"user_id": "7", "token": {"access_token": "new-access"}},
        device_uuid="uuid",
        previous_refresh_token="old-refresh",
    )

    assert result.refresh_token == "old-refresh"


@pytest.mark.asyncio
async def test_401_refreshes_once_persists_rotation_and_retries() -> None:
    api = AsyncMock(spec=HehkuApi)
    api.get_locations.side_effect = [HehkuAuthError("expired"), [{"id": "location"}]]
    api.refresh.return_value = credentials("new-access", "new-refresh")
    persisted: list[Credentials] = []
    client = HehkuClient(api, credentials(), persisted.append)

    result = await client.get_locations()

    assert result == [{"id": "location"}]
    api.refresh.assert_awaited_once_with(credentials())
    assert api.get_locations.await_args_list[1].args == ("7", "new-access")
    assert persisted == [credentials("new-access", "new-refresh")]


@pytest.mark.asyncio
async def test_non_auth_error_is_not_retried() -> None:
    api = AsyncMock(spec=HehkuApi)
    error = RuntimeError("offline")
    api.get_locations.side_effect = error
    client = HehkuClient(api, credentials(), lambda _: None)

    with pytest.raises(RuntimeError, match="offline"):
        await client.get_locations()

    api.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_prices_use_authenticated_client() -> None:
    api = AsyncMock(spec=HehkuApi)
    api.get_market_prices.return_value = {
        "resolution": "hour",
        "currency": "EUR",
        "from": "2026-08-01T00:00:00",
        "to": "2026-08-02T00:00:00",
        "values": [],
    }
    client = HehkuClient(api, credentials(), lambda _: None)

    result = await client.get_market_prices(42, "2026-08-01T00:00:00", "2026-08-02T00:00:00")

    assert result["currency"] == "EUR"
    api.get_market_prices.assert_awaited_once_with(
        42,
        "2026-08-01T00:00:00",
        "2026-08-02T00:00:00",
        "old-access",
    )
