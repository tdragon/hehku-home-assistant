"""Set up the unofficial Hehku Energia integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Credentials, HehkuApi, HehkuClient
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END_DATE,
    ATTR_START_DATE,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_UUID,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_REFRESH_TOKEN,
    CONF_TIME_ZONE,
    CONF_USER_ID,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    PLATFORMS,
    SERVICE_BACKFILL,
)
from .coordinator import HehkuCoordinator
from .statistics import HehkuStatisticsImporter

DATA_COORDINATORS = "coordinators"

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the parameterized historical backfill action."""
    hass.data.setdefault(DOMAIN, {DATA_COORDINATORS: {}})

    async def async_handle_backfill(call: ServiceCall) -> None:
        coordinators: dict[str, HehkuCoordinator] = hass.data[DOMAIN][DATA_COORDINATORS]
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id is not None:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                raise ServiceValidationError("Unknown or unloaded Hehku config entry")
        elif len(coordinators) == 1:
            coordinator = next(iter(coordinators.values()))
        else:
            raise ServiceValidationError(
                "config_entry_id is required when more than one Hehku entry is loaded"
            )

        start_date: date = call.data[ATTR_START_DATE]
        end_date: date = call.data.get(ATTR_END_DATE) or (
            datetime.now(coordinator.timezone).date() + timedelta(days=1)
        )
        if end_date <= start_date:
            raise ServiceValidationError("end_date must be after start_date")
        if (end_date - start_date).days > MAX_BACKFILL_DAYS:
            raise ServiceValidationError(f"A backfill may span at most {MAX_BACKFILL_DAYS} days")
        await coordinator.async_backfill(start_date, end_date)

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        async_handle_backfill,
        schema=BACKFILL_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Hehku config entry."""
    credentials = Credentials(
        user_id=entry.data[CONF_USER_ID],
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        device_uuid=entry.data[CONF_DEVICE_UUID],
    )

    def async_persist_credentials(updated: Credentials) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_USER_ID: updated.user_id,
                CONF_ACCESS_TOKEN: updated.access_token,
                CONF_REFRESH_TOKEN: updated.refresh_token,
                CONF_DEVICE_UUID: updated.device_uuid,
            },
        )

    api = HehkuApi(async_get_clientsession(hass))
    client = HehkuClient(api, credentials, async_persist_credentials)
    importer = HehkuStatisticsImporter(
        hass,
        client,
        entry.data[CONF_LOCATION_ID],
        entry.data[CONF_LOCATION_NAME],
        entry.data[CONF_TIME_ZONE],
    )
    coordinator = HehkuCoordinator(hass, entry, importer)
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][DATA_COORDINATORS][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Hehku config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN][DATA_COORDINATORS].pop(entry.entry_id, None)
    return True
