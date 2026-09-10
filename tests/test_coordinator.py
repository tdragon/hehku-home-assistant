"""Tests for coordinator error translation."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hehku_energy.api import HehkuApiError, HehkuConnectionError
from custom_components.hehku_energy.coordinator import HehkuCoordinator


@pytest.mark.parametrize("error", [HehkuApiError("bad payload"), HehkuConnectionError("offline")])
async def test_backfill_translates_api_errors(hass: HomeAssistant, error: Exception) -> None:
    """Manual actions should expose controlled Home Assistant service errors."""
    importer = MagicMock()
    importer.timezone = "Europe/Helsinki"
    importer.async_import = AsyncMock(side_effect=error)
    price_importer = MagicMock()
    coordinator = HehkuCoordinator(hass, MagicMock(), importer, price_importer)

    with pytest.raises(ServiceValidationError):
        await coordinator.async_backfill(date(2026, 8, 1), date(2026, 8, 2))


async def test_delayed_consumption_retry_skips_when_yesterday_is_complete(
    hass: HomeAssistant,
) -> None:
    importer = MagicMock()
    importer.timezone = "Europe/Helsinki"
    importer.async_day_complete = AsyncMock(return_value=True)
    coordinator = HehkuCoordinator(hass, MagicMock(), importer, MagicMock())
    coordinator.async_refresh_consumption = AsyncMock()

    await coordinator._async_scheduled_consumption(datetime(2026, 9, 10, 1, 15, tzinfo=UTC))

    importer.async_day_complete.assert_awaited_once()
    coordinator.async_refresh_consumption.assert_not_awaited()


async def test_primary_price_poll_runs_without_completeness_probe(hass: HomeAssistant) -> None:
    importer = MagicMock()
    importer.timezone = "Europe/Helsinki"
    price_importer = MagicMock()
    price_importer.async_day_complete = AsyncMock(return_value=True)
    coordinator = HehkuCoordinator(hass, MagicMock(), importer, price_importer)
    coordinator.async_refresh_prices = AsyncMock()

    await coordinator._async_scheduled_prices(datetime(2026, 9, 10, 12, 15, tzinfo=UTC))

    price_importer.async_day_complete.assert_not_awaited()
    coordinator.async_refresh_prices.assert_awaited_once()
