"""Tests for the Home Assistant external-statistics importer."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import UnitOfEnergy

from custom_components.hehku_energy.api import HehkuApiError
from custom_components.hehku_energy.statistics import HehkuStatisticsImporter
from custom_components.hehku_energy.statistics_core import ExistingStatistic


async def test_imports_raw_wh_as_hourly_kwh_statistics() -> None:
    """Eliq's energy values are Wh despite the generic API unit name."""
    client = MagicMock()
    client.get_consumption = AsyncMock(
        return_value={
            "consumption": [500, None, 1250] + [None] * 21,
            "resolution": "hour",
            "fuel": "elec",
            "unit": "energy",
            "from": "2026-08-01T00:00:00",
            "to": "2026-08-02T00:00:00",
        }
    )
    importer = HehkuStatisticsImporter(MagicMock(), client, 42, "Apartment", "Europe/Helsinki")
    importer._effective_end_date = AsyncMock(return_value=date(2026, 8, 2))
    importer._existing_statistics = AsyncMock(return_value={})

    with patch(
        "custom_components.hehku_energy.statistics.async_add_external_statistics"
    ) as add_statistics:
        result = await importer.async_import(date(2026, 8, 1), date(2026, 8, 2))

    metadata = add_statistics.call_args.args[1]
    rows = add_statistics.call_args.args[2]
    assert metadata["has_mean"] is False
    assert metadata["has_sum"] is True
    assert metadata["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    assert rows == [
        {
            "start": datetime(2026, 7, 31, 21, tzinfo=UTC),
            "state": 0.5,
            "sum": 0.5,
        },
        {
            "start": datetime(2026, 7, 31, 23, tzinfo=UTC),
            "state": 1.25,
            "sum": 1.75,
        },
    ]
    assert result.imported == 2
    assert result.missing == 22


async def test_import_uses_latest_sum_before_gap() -> None:
    """A missing preceding hour must not reset the cumulative statistic."""
    client = MagicMock()
    client.get_consumption = AsyncMock(
        return_value={
            "consumption": [1000] + [None] * 23,
            "resolution": "hour",
            "fuel": "elec",
        }
    )
    importer = HehkuStatisticsImporter(MagicMock(), client, 42, "Apartment", "Europe/Helsinki")
    importer._effective_end_date = AsyncMock(return_value=date(2026, 8, 2))
    importer._latest_statistic_before = AsyncMock(
        return_value=ExistingStatistic(state=2.0, sum=10.0)
    )
    importer._existing_statistics = AsyncMock(return_value={})

    with patch(
        "custom_components.hehku_energy.statistics.async_add_external_statistics"
    ) as add_statistics:
        await importer.async_import(date(2026, 8, 1), date(2026, 8, 2))

    rows = add_statistics.call_args.args[2]
    assert rows[0]["sum"] == 11.0


async def test_effective_import_span_is_limited() -> None:
    """Automatic cumulative repair must not bypass the API safety limit."""
    importer = HehkuStatisticsImporter(MagicMock(), MagicMock(), 42, "Apartment", "Europe/Helsinki")
    importer._effective_end_date = AsyncMock(return_value=date(2026, 1, 2))
    importer._fetch = AsyncMock()

    with pytest.raises(HehkuApiError, match="effective import may span at most"):
        await importer.async_import(date(2022, 1, 1), date(2022, 1, 2))

    importer._fetch.assert_not_awaited()
