"""Polling coordinator for Hehku Energia."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HehkuAuthError, HehkuConnectionError, HehkuError
from .const import DOMAIN, POLL_INTERVAL, TRAILING_DAYS
from .statistics import HehkuStatisticsImporter, ImportResult

_LOGGER = logging.getLogger(__name__)


class HehkuCoordinator(DataUpdateCoordinator[ImportResult]):
    """Fetch a correction window once an hour and import external statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        importer: HehkuStatisticsImporter,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.importer = importer
        self.timezone = ZoneInfo(importer.timezone)

    async def _async_update_data(self) -> ImportResult:
        today = datetime.now(self.timezone).date()
        try:
            return await self.importer.async_import(
                today - timedelta(days=TRAILING_DAYS), today + timedelta(days=1)
            )
        except HehkuAuthError as err:
            raise ConfigEntryAuthFailed from err
        except HehkuConnectionError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        except HehkuError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_backfill(self, start_date: date, end_date: date) -> ImportResult:
        """Run an explicit historical import and update entities."""
        try:
            result = await self.importer.async_import(start_date, end_date)
        except HehkuAuthError as err:
            raise ConfigEntryAuthFailed from err
        except HehkuConnectionError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err
        except HehkuError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err
        self.async_set_updated_data(result)
        return result
