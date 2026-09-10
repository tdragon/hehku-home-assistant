"""Availability-aware coordinator for Hehku Energia."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HehkuAuthError, HehkuConnectionError, HehkuError
from .const import DOMAIN, TRAILING_DAYS
from .price_statistics import HehkuPriceImporter, PriceImportResult
from .pricing_core import CostSummary
from .statistics import HehkuStatisticsImporter, ImportResult

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HehkuData:
    """Latest consumption and price/cost import summaries."""

    consumption: ImportResult
    pricing: PriceImportResult


def empty_price_result() -> PriceImportResult:
    """Return an empty price result before the first successful price import."""
    return PriceImportResult(
        imported_prices=0,
        missing_prices=0,
        imported_costs=0,
        missing_costs=0,
        current_price=None,
        summary=CostSummary(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0),
        completed_at=datetime.now(UTC),
    )


class HehkuCoordinator(DataUpdateCoordinator[HehkuData]):
    """Import data near its publication times and on explicit backfill."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        importer: HehkuStatisticsImporter,
        price_importer: HehkuPriceImporter,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.entry = entry
        self.importer = importer
        self.price_importer = price_importer
        self.timezone = ZoneInfo(importer.timezone)
        self._schedule_unsubs: list[CALLBACK_TYPE] = []

    async def _async_update_data(self) -> HehkuData:
        today = datetime.now(self.timezone).date()
        try:
            consumption = await self.importer.async_import(
                today - timedelta(days=TRAILING_DAYS), today + timedelta(days=1)
            )
            price_start = min(today - timedelta(days=TRAILING_DAYS), today.replace(day=1))
            try:
                pricing = await self.price_importer.async_import(
                    price_start,
                    today + timedelta(days=1),
                    consumption_overrides=self.importer.latest_intervals,
                )
            except HehkuAuthError:
                raise
            except HehkuError:
                _LOGGER.warning(
                    "Initial Hehku market-price import failed; consumption remains available",
                    exc_info=True,
                )
                pricing = empty_price_result()
            return HehkuData(consumption, pricing)
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

    @callback
    def async_start_schedules(self) -> None:
        """Schedule primary publication checks and bounded delayed retries."""
        if self._schedule_unsubs:
            return
        self._schedule_unsubs.extend(
            (
                async_track_utc_time_change(
                    self.hass,
                    self._async_scheduled_consumption,
                    hour=[0, 1, 2],
                    minute=15,
                    second=0,
                ),
                async_track_utc_time_change(
                    self.hass,
                    self._async_scheduled_prices,
                    hour=[12, 13, 14],
                    minute=15,
                    second=0,
                ),
            )
        )

    @callback
    def async_stop_schedules(self) -> None:
        """Cancel publication-time listeners."""
        for unsubscribe in self._schedule_unsubs:
            unsubscribe()
        self._schedule_unsubs.clear()

    async def _async_scheduled_consumption(self, now: datetime) -> None:
        local_today = now.astimezone(self.timezone).date()
        if now.hour != 0 and await self.importer.async_day_complete(
            local_today - timedelta(days=1)
        ):
            return
        try:
            await self.async_refresh_consumption()
        except ConfigEntryAuthFailed:
            self.entry.async_start_reauth(self.hass)
            _LOGGER.warning("Scheduled Hehku consumption refresh requires reauthentication")
        except UpdateFailed:
            _LOGGER.warning("Scheduled Hehku consumption refresh failed", exc_info=True)

    async def _async_scheduled_prices(self, now: datetime) -> None:
        local_today = now.astimezone(self.timezone).date()
        if now.hour != 12 and await self.price_importer.async_day_complete(
            local_today + timedelta(days=1)
        ):
            return
        try:
            await self.async_refresh_prices()
        except ConfigEntryAuthFailed:
            self.entry.async_start_reauth(self.hass)
            _LOGGER.warning("Scheduled Hehku market-price refresh requires reauthentication")
        except UpdateFailed:
            _LOGGER.warning("Scheduled Hehku market-price refresh failed", exc_info=True)

    async def async_refresh_consumption(self) -> HehkuData:
        """Fetch the correction window and update costs from stored prices."""
        today = datetime.now(self.timezone).date()
        try:
            consumption = await self.importer.async_import(
                today - timedelta(days=TRAILING_DAYS), today + timedelta(days=1)
            )
            pricing = await self.price_importer.async_recalculate(
                min(today - timedelta(days=TRAILING_DAYS), today.replace(day=1)),
                today + timedelta(days=1),
                consumption_overrides=self.importer.latest_intervals,
            )
        except HehkuError as err:
            raise self._update_error(err) from err
        result = HehkuData(consumption, pricing)
        self.async_set_updated_data(result)
        return result

    async def async_refresh_prices(self) -> HehkuData:
        """Fetch today and tomorrow market prices and refresh estimates."""
        today = datetime.now(self.timezone).date()
        try:
            pricing = await self.price_importer.async_import(today, today + timedelta(days=2))
        except HehkuError as err:
            raise self._update_error(err) from err
        consumption = self.data.consumption
        result = HehkuData(consumption, pricing)
        self.async_set_updated_data(result)
        return result

    async def async_backfill(self, start_date: date, end_date: date) -> ImportResult:
        """Backfill consumption and recalculate any locally priced intervals."""
        try:
            consumption = await self.importer.async_import(start_date, end_date)
            pricing = await self.price_importer.async_recalculate(
                start_date,
                end_date,
                consumption_overrides=self.importer.latest_intervals,
            )
        except HehkuError as err:
            raise self._service_error(err) from err
        self.async_set_updated_data(HehkuData(consumption, pricing))
        return consumption

    async def async_backfill_prices(self, start_date: date, end_date: date) -> PriceImportResult:
        """Backfill spot prices and rebuild daily/month-to-date estimates."""
        try:
            pricing = await self.price_importer.async_import(start_date, end_date)
        except HehkuError as err:
            raise self._service_error(err) from err
        self.async_set_updated_data(HehkuData(self.data.consumption, pricing))
        return pricing

    @staticmethod
    def _update_error(error: HehkuError) -> ConfigEntryAuthFailed | UpdateFailed:
        if isinstance(error, HehkuAuthError):
            return ConfigEntryAuthFailed()
        if isinstance(error, HehkuConnectionError):
            return UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect")
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(error)},
        )

    @staticmethod
    def _service_error(error: HehkuError) -> ConfigEntryAuthFailed | ServiceValidationError:
        if isinstance(error, HehkuAuthError):
            return ConfigEntryAuthFailed()
        if isinstance(error, HehkuConnectionError):
            return ServiceValidationError(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            )
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(error)},
        )
