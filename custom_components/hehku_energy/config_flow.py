"""Configuration flow for Hehku Energia magic-link login."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    Credentials,
    HehkuApi,
    HehkuError,
    HehkuLoginError,
    LoginTicket,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_UUID,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_MARGIN,
    CONF_REFRESH_TOKEN,
    CONF_SPOT_MULTIPLIER,
    CONF_TIME_ZONE,
    CONF_USER_ID,
    DEFAULT_MARGIN,
    DEFAULT_SPOT_MULTIPLIER,
    DEFAULT_TIME_ZONE,
    DOMAIN,
)

POLL_SECONDS = 4
LOGIN_TIMEOUT_SECONDS = 30 * 60
TERMINAL_TICKET_STATUSES = {"cancelled", "canceled", "expired", "failed", "error"}


class HehkuConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Hehku Energia config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the local supply-price options flow."""
        return HehkuOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._api: HehkuApi | None = None
        self._ticket: LoginTicket | None = None
        self._device_uuid = ""
        self._login_task: asyncio.Task[Credentials] | None = None
        self._credentials: Credentials | None = None
        self._locations: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Request a magic link."""
        return await self._async_email_step("user", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a fresh magic link for an existing entry."""
        return await self._async_email_step("reauth_confirm", user_input)

    async def _async_email_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._api = HehkuApi(async_get_clientsession(self.hass))
                self._ticket = await self._api.request_magic_link(user_input[CONF_EMAIL])
            except HehkuError:
                errors["base"] = "cannot_connect"
            else:
                self._device_uuid = (
                    self._reauth_entry.data[CONF_DEVICE_UUID]
                    if self._reauth_entry is not None
                    else str(uuid.uuid4())
                )
                return await self.async_step_wait_for_link()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): cv.string}),
            errors=errors,
        )

    async def _async_wait_for_link(self) -> Credentials:
        if self._api is None or self._ticket is None:
            raise HehkuLoginError("Login was not initialized")
        ticket = self._ticket
        async with asyncio.timeout(LOGIN_TIMEOUT_SECONDS):
            while ticket.status != "completed":
                if ticket.status in TERMINAL_TICKET_STATUSES:
                    raise HehkuLoginError(f"Login ticket ended with status {ticket.status}")
                await asyncio.sleep(POLL_SECONDS)
                ticket = await self._api.get_ticket(ticket.ticket_id)
            return await self._api.exchange_ticket(ticket.ticket_id, self._device_uuid)

    async def async_step_wait_for_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show progress while the user opens the email link."""
        if self._login_task is None:
            self._login_task = self.hass.async_create_task(self._async_wait_for_link())
        if not self._login_task.done():
            return self.async_show_progress(
                step_id="wait_for_link",
                progress_action="wait_for_link",
                progress_task=self._login_task,
            )
        try:
            self._credentials = await self._login_task
        except (HehkuError, TimeoutError):
            return self.async_show_progress_done(next_step_id="login_failed")
        finally:
            self._login_task = None
        return self.async_show_progress_done(next_step_id="finish_login")

    async def async_step_login_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to restart failed or expired login."""
        if user_input is not None:
            if self._reauth_entry is not None:
                return await self.async_step_reauth_confirm()
            return await self.async_step_user()
        return self.async_show_form(step_id="login_failed")

    async def async_step_finish_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover locations after successful authentication."""
        if self._api is None or self._credentials is None:
            return self.async_abort(reason="login_not_initialized")
        try:
            self._locations = await self._api.get_locations(
                self._credentials.user_id, self._credentials.access_token
            )
        except HehkuError:
            return self.async_abort(reason="cannot_connect")
        if not self._locations:
            return self.async_abort(reason="no_locations")

        if self._reauth_entry is not None:
            location_id = self._reauth_entry.data[CONF_LOCATION_ID]
            return await self._async_create_or_update(location_id)
        if len(self._locations) == 1:
            return await self._async_create_or_update(self._locations[0]["id"])
        return await self.async_step_location()

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one of several metering locations."""
        choices = {
            str(location["id"]): str(
                location.get("name") or location.get("ext_ref") or f"Location {index + 1}"
            )
            for index, location in enumerate(self._locations)
        }
        if user_input is not None:
            return await self._async_create_or_update(user_input[CONF_LOCATION_ID])
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema({vol.Required(CONF_LOCATION_ID): vol.In(choices)}),
        )

    async def _async_create_or_update(self, location_id: int | str) -> ConfigFlowResult:
        if self._credentials is None:
            return self.async_abort(reason="login_not_initialized")
        location = next(
            (item for item in self._locations if str(item.get("id")) == str(location_id)),
            None,
        )
        if location is None:
            return self.async_abort(reason="location_not_found")
        location_name = str(location.get("name") or location.get("ext_ref") or "Hehku")
        data = {
            CONF_USER_ID: self._credentials.user_id,
            CONF_ACCESS_TOKEN: self._credentials.access_token,
            CONF_REFRESH_TOKEN: self._credentials.refresh_token,
            CONF_DEVICE_UUID: self._credentials.device_uuid,
            CONF_LOCATION_ID: location["id"],
            CONF_LOCATION_NAME: location_name,
            # Eliq has returned Europe/Kiev for a Finnish location. Use Finland's
            # actual IANA zone until the API's location metadata is trustworthy.
            CONF_TIME_ZONE: DEFAULT_TIME_ZONE,
        }
        await self.async_set_unique_id(f"{self._credentials.user_id}:{location['id']}")
        if self._reauth_entry is not None:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(self._reauth_entry, data_updates=data)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=location_name, data=data)


class HehkuOptionsFlow(config_entries.OptionsFlow):
    """Configure local spot-price calculation parameters."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit spot multiplier and VAT-inclusive margin."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SPOT_MULTIPLIER,
                        default=options.get(CONF_SPOT_MULTIPLIER, DEFAULT_SPOT_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
                    vol.Required(
                        CONF_MARGIN,
                        default=options.get(CONF_MARGIN, DEFAULT_MARGIN),
                    ): vol.All(vol.Coerce(float), vol.Range(min=-1, max=1)),
                }
            ),
        )
