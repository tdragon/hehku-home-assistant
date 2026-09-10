"""Tests for market-price and calculated-cost statistics."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.recorder.core import Recorder
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)

from custom_components.hehku_energy.api import HehkuApiError
from custom_components.hehku_energy.price_statistics import (
    HehkuPriceImporter,
    cost_statistic_id_for_location,
    price_statistic_id_for_location,
)
from custom_components.hehku_energy.pricing_core import CostSummary


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Create the recorder database before the Home Assistant fixture."""


async def test_fetches_and_maps_hourly_eur_prices() -> None:
    client = MagicMock()
    client.get_market_prices = AsyncMock(
        return_value={
            "resolution": "hour",
            "currency": "EUR",
            "from": "2026-08-01T00:00:00",
            "to": "2026-08-02T00:00:00",
            "values": [
                {
                    "period_start": f"2026-08-01T{hour:02d}:00:00",
                    "price_kwh": 0.01 + hour / 1000,
                }
                for hour in range(24)
            ],
        }
    )
    importer = HehkuPriceImporter(
        MagicMock(), client, 42, "Apartment", "Europe/Helsinki", 1.0, 0.0035
    )

    prices, missing = await importer._fetch_prices(date(2026, 8, 1), date(2026, 8, 2))

    assert missing == 0
    assert prices[datetime(2026, 7, 31, 21, tzinfo=UTC)] == 0.01
    assert prices[datetime(2026, 8, 1, 20, tzinfo=UTC)] == 0.033


async def test_rejects_non_eur_market_prices() -> None:
    client = MagicMock()
    client.get_market_prices = AsyncMock(
        return_value={
            "resolution": "hour",
            "currency": "SEK",
            "from": "2026-08-01T00:00:00",
            "to": "2026-08-02T00:00:00",
            "values": [],
        }
    )
    importer = HehkuPriceImporter(
        MagicMock(), client, 42, "Apartment", "Europe/Helsinki", 1.0, 0.0035
    )

    with pytest.raises(HehkuApiError, match="EUR"):
        await importer._fetch_prices(date(2026, 8, 1), date(2026, 8, 2))


async def test_import_writes_price_and_correction_safe_cost_statistics() -> None:
    hass = MagicMock()
    client = MagicMock()
    importer = HehkuPriceImporter(hass, client, 42, "Apartment", "Europe/Helsinki", 1.0, 0.0035)
    start = datetime(2026, 7, 31, 21, tzinfo=UTC)
    prices = {start: 0.10, start.replace(hour=22): 0.20}
    importer._fetch_prices = AsyncMock(return_value=(prices, 0))
    importer._read_consumption = AsyncMock(return_value={})
    importer._effective_cost_end = AsyncMock(return_value=start.replace(hour=23))
    importer._latest_cost_before = AsyncMock(return_value=None)
    importer._existing_cost_statistics = AsyncMock(return_value={})
    importer._async_summary = AsyncMock(
        return_value=CostSummary(0.5, 0.01, 0.51, 2, 0.5, 0.01, 0.51, 2)
    )
    importer._current_price = AsyncMock(return_value=0.2)

    with patch(
        "custom_components.hehku_energy.price_statistics.async_add_external_statistics"
    ) as add_statistics:
        result = await importer.async_import(
            date(2026, 8, 1),
            date(2026, 8, 2),
            consumption_overrides={start: 1.0, start.replace(hour=22): 2.0},
        )

    assert add_statistics.call_count == 2
    price_metadata, price_rows = add_statistics.call_args_list[0].args[1:]
    assert price_metadata["statistic_id"] == price_statistic_id_for_location(42)
    assert price_rows[0]["mean"] == 0.10
    cost_metadata, cost_rows = add_statistics.call_args_list[1].args[1:]
    assert cost_metadata["statistic_id"] == cost_statistic_id_for_location(42)
    assert cost_rows[0]["state"] == pytest.approx(0.1035)
    assert cost_rows[1]["sum"] == pytest.approx(0.5105)
    assert result.imported_prices == 2
    assert result.imported_costs == 2


async def test_cost_rebuild_rejects_automatic_extension_beyond_safety_limit() -> None:
    importer = HehkuPriceImporter(
        MagicMock(), MagicMock(), 42, "Apartment", "Europe/Helsinki", 1.0, 0.0035
    )
    start = datetime(2020, 1, 1, tzinfo=UTC)
    importer._read_consumption = AsyncMock(return_value={start: 1.0})
    importer._effective_cost_end = AsyncMock(return_value=start + timedelta(days=1100))

    with pytest.raises(HehkuApiError, match="maximum safe rebuild range"):
        await importer._write_costs({start: 0.10})


async def test_price_statistics_round_trip_through_recorder(
    hass: HomeAssistant, recorder_mock: Recorder
) -> None:
    assert recorder_mock is not None
    importer = HehkuPriceImporter(
        hass,
        MagicMock(),
        42,
        "Apartment",
        "Europe/Helsinki",
        1.0,
        0.0035,
    )
    start = datetime(2026, 9, 10, 8, tzinfo=UTC)

    importer._write_prices({start: 0.06467})
    await async_recorder_block_till_done(hass)

    assert await importer._read_prices(start, start + timedelta(hours=1)) == {
        start: pytest.approx(0.06467)
    }


async def test_recent_price_cache_is_available_before_recorder_commit() -> None:
    importer = HehkuPriceImporter(
        MagicMock(),
        MagicMock(),
        42,
        "Apartment",
        "Europe/Helsinki",
        1.0,
        0.0035,
    )
    importer._statistics = AsyncMock(return_value={})
    start = datetime(2026, 9, 10, 8, tzinfo=UTC)

    with patch("custom_components.hehku_energy.price_statistics.async_add_external_statistics"):
        importer._write_prices({start: 0.06467})

    assert await importer._read_prices(start, start + timedelta(hours=1)) == {
        start: pytest.approx(0.06467)
    }
