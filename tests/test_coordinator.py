"""Tests for coordinator error translation."""

from datetime import date
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
    coordinator = HehkuCoordinator(hass, MagicMock(), importer)

    with pytest.raises(ServiceValidationError):
        await coordinator.async_backfill(date(2026, 8, 1), date(2026, 8, 2))
