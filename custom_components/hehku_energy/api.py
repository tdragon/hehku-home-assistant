"""Asynchronous client for the private Hehku/Eliq Insights API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from aiohttp import ClientError, ClientSession

API_BASE = "https://hehku.insights-api.eliq.com/v3"
CLIENT_ID = 20189927261
USER_AGENT = "hehku-home-assistant/0.1.0"

T = TypeVar("T")


class HehkuError(Exception):
    """Base exception for sanitized Hehku errors."""


class HehkuAuthError(HehkuError):
    """Authentication failed or expired."""


class HehkuConnectionError(HehkuError):
    """The API could not be reached."""


class HehkuApiError(HehkuError):
    """The API returned an unexpected response."""


class HehkuLoginError(HehkuError):
    """The interactive magic-link flow failed."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """Renewable Eliq credentials."""

    user_id: int | str
    access_token: str
    refresh_token: str
    device_uuid: str


@dataclass(frozen=True, slots=True)
class LoginTicket:
    """Magic-link login ticket."""

    ticket_id: str
    status: str


def parse_token_response(
    payload: dict[str, Any],
    *,
    device_uuid: str,
    previous_refresh_token: str | None = None,
) -> Credentials:
    """Validate a token response without logging secret fields."""
    token = payload.get("token")
    if not isinstance(token, dict):
        raise HehkuApiError("Token response does not contain a token object")

    user_id = payload.get("user_id")
    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token") or previous_refresh_token
    if user_id is None or not isinstance(access_token, str) or not access_token:
        raise HehkuApiError("Token response is missing the user or access token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HehkuApiError("Token response does not contain a refresh token")
    return Credentials(user_id, access_token, refresh_token, device_uuid)


def parse_ticket(payload: dict[str, Any]) -> LoginTicket:
    """Validate either observed ticket response shape."""
    ticket = payload.get("ticket")
    if not isinstance(ticket, dict):
        ticket = payload
    ticket_id = ticket.get("id")
    status = ticket.get("status", "unknown")
    if not isinstance(ticket_id, str) or not ticket_id:
        raise HehkuApiError("Login response does not contain a ticket ID")
    return LoginTicket(ticket_id, str(status).lower())


class HehkuApi:
    """Low-level API transport. Full auth URLs and secrets are never logged."""

    def __init__(
        self,
        session: ClientSession,
        *,
        base_url: str = API_BASE,
        timeout: float = 30.0,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any:
        normalized_path = "/" + path.lstrip("/")
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Xapp": json.dumps(
                {"platform": "desktop", "isNative": False, "xVersion": "0.1.0"},
                separators=(",", ":"),
            ),
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    self._base_url + normalized_path,
                    params=params,
                    json=body,
                    headers=headers,
                ) as response:
                    raw = await response.read()
                    status = response.status
                    reason = response.reason
        except (TimeoutError, ClientError) as err:
            raise HehkuConnectionError("Unable to connect to Hehku") from err

        if status == 401:
            raise HehkuAuthError("Hehku authentication was rejected")
        if status >= 400:
            # The OAuth query contains the refresh token. Deliberately omit URLs,
            # response bodies, params and arbitrary server diagnostics here.
            raise HehkuApiError(f"Hehku request failed with HTTP {status} {reason or ''}".strip())
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise HehkuApiError("Hehku returned a non-JSON response") from err

    async def request_magic_link(self, email: str) -> LoginTicket:
        """Request an email magic link."""
        payload = await self._request_json(
            "POST",
            "/authentication/login/magiclink",
            body={"client_id": CLIENT_ID, "type": "email", "user_reference": email},
        )
        if not isinstance(payload, dict):
            raise HehkuApiError("Unexpected magic-link response")
        return parse_ticket(payload)

    async def get_ticket(self, ticket_id: str) -> LoginTicket:
        """Get magic-link ticket status."""
        payload = await self._request_json("GET", f"/authentication/tickets/{ticket_id}")
        if not isinstance(payload, dict):
            raise HehkuApiError("Unexpected ticket response")
        return parse_ticket(payload)

    async def exchange_ticket(self, ticket_id: str, device_uuid: str) -> Credentials:
        """Exchange a completed login ticket for renewable credentials."""
        payload = await self._request_json(
            "GET",
            "/authentication/oauth/token",
            params={
                "grant_type": "ticket",
                "client_id": CLIENT_ID,
                "ticket_id": ticket_id,
                "device_uuid": device_uuid,
            },
        )
        if not isinstance(payload, dict):
            raise HehkuApiError("Unexpected token response")
        return parse_token_response(payload, device_uuid=device_uuid)

    async def refresh(self, credentials: Credentials) -> Credentials:
        """Refresh an expired token and retain/rotate the refresh token."""
        payload = await self._request_json(
            "GET",
            "/authentication/oauth/token",
            params={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "user_id": credentials.user_id,
                "refresh_token": credentials.refresh_token,
                "device_uuid": credentials.device_uuid,
            },
        )
        if not isinstance(payload, dict):
            raise HehkuApiError("Unexpected refresh response")
        return parse_token_response(
            payload,
            device_uuid=credentials.device_uuid,
            previous_refresh_token=credentials.refresh_token,
        )

    async def get_locations(self, user_id: int | str, access_token: str) -> list[dict[str, Any]]:
        """List metering locations."""
        payload = await self._request_json(
            "GET", f"/users/{user_id}/locations", access_token=access_token
        )
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise HehkuApiError("Locations response is not an array of objects")
        return payload

    async def get_consumption(
        self,
        location_id: int | str,
        start: str,
        end: str,
        access_token: str,
    ) -> dict[str, Any]:
        """Fetch positional hourly electricity consumption in raw Wh."""
        payload = await self._request_json(
            "GET",
            f"/locations/{location_id}/consumption",
            params={
                "fuel": "elec",
                "unit": "energy",
                "resolution": "hour",
                "from": start,
                "to": end,
                "include_incomplete": "true",
            },
            access_token=access_token,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("consumption"), list):
            raise HehkuApiError("Consumption response does not contain an array")
        return payload


class HehkuClient:
    """Authenticated client which serializes refresh-token rotation."""

    def __init__(
        self,
        api: HehkuApi,
        credentials: Credentials,
        credentials_updated: Callable[[Credentials], None],
    ) -> None:
        self.api = api
        self.credentials = credentials
        self._credentials_updated = credentials_updated
        self._refresh_lock = asyncio.Lock()

    async def _authenticated(self, operation: Callable[[Credentials], Awaitable[T]]) -> T:
        attempted = self.credentials
        try:
            return await operation(attempted)
        except HehkuAuthError:
            pass

        async with self._refresh_lock:
            if self.credentials.access_token == attempted.access_token:
                self.credentials = await self.api.refresh(self.credentials)
                # Persist a rotated refresh token before retrying any operation.
                self._credentials_updated(self.credentials)
        return await operation(self.credentials)

    async def get_locations(self) -> list[dict[str, Any]]:
        """List locations, refreshing once after an HTTP 401."""
        return await self._authenticated(
            lambda current: self.api.get_locations(current.user_id, current.access_token)
        )

    async def get_consumption(self, location_id: int | str, start: str, end: str) -> dict[str, Any]:
        """Fetch consumption, refreshing once after an HTTP 401."""
        return await self._authenticated(
            lambda current: self.api.get_consumption(location_id, start, end, current.access_token)
        )
