"""Import Eliq market prices and locally calculated supply costs."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

from .api import HehkuApiError, HehkuClient
from .const import DOMAIN, MAX_BACKFILL_DAYS
from .intervals import IntervalAlignmentError, dst_safe_date_chunks, expected_interval_count
from .pricing_core import CostSummary, IntervalCost, calculate_costs, summarize_costs
from .statistics import statistic_id_for_location
from .statistics_core import ExistingStatistic, rebuild_statistics

_LOGGER = logging.getLogger(__name__)
_STATISTICS_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PRICE_UNIT = "EUR/kWh"
CURRENCY = "EUR"


@dataclass(frozen=True, slots=True)
class PriceImportResult:
    """Summary of the last price/cost refresh."""

    imported_prices: int
    missing_prices: int
    imported_costs: int
    missing_costs: int
    current_price: float | None
    summary: CostSummary
    completed_at: datetime


def _safe_object_id(location_id: int | str) -> str:
    object_id = re.sub(r"[^a-z0-9_]", "_", str(location_id).lower()).strip("_")
    if not object_id:
        raise HehkuApiError("The location ID cannot form a statistic ID")
    return object_id


def price_statistic_id_for_location(location_id: int | str) -> str:
    """Return a stable external-statistic ID for raw market prices."""
    return f"{DOMAIN}:{_safe_object_id(location_id)}_spot_price"


def cost_statistic_id_for_location(location_id: int | str) -> str:
    """Return a stable external-statistic ID for variable supply cost."""
    return f"{DOMAIN}:{_safe_object_id(location_id)}_supply_cost"


class HehkuPriceImporter:
    """Fetch prices, join consumption, and import correction-safe costs."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: HehkuClient,
        location_id: int | str,
        location_name: str,
        timezone: str,
        spot_multiplier: float,
        margin: float,
    ) -> None:
        self.hass = hass
        self.client = client
        self.location_id = location_id
        self.location_name = location_name
        self.timezone = timezone
        self.zone = ZoneInfo(timezone)
        self.spot_multiplier = spot_multiplier
        self.margin = margin
        self.price_statistic_id = price_statistic_id_for_location(location_id)
        self.cost_statistic_id = cost_statistic_id_for_location(location_id)
        self.consumption_statistic_id = statistic_id_for_location(location_id)
        self._lock = asyncio.Lock()

    async def async_import(
        self,
        start_date: date,
        end_date: date,
        *,
        now: datetime | None = None,
        consumption_overrides: Mapping[datetime, float | None] | None = None,
    ) -> PriceImportResult:
        """Backfill raw spot prices and rebuild affected variable costs."""
        if end_date <= start_date:
            raise HehkuApiError("Price backfill end_date must be after start_date")
        if (end_date - start_date).days > MAX_BACKFILL_DAYS:
            raise HehkuApiError(f"A price backfill may span at most {MAX_BACKFILL_DAYS} days")
        async with self._lock:
            prices, missing_prices = await self._fetch_prices(start_date, end_date)
            self._write_prices(prices)
            imported_costs, missing_costs = await self._write_costs(
                prices, consumption_overrides=consumption_overrides
            )
            effective_now = now or datetime.now(UTC)
            summary = await self._async_summary(
                effective_now,
                price_overrides=prices,
                consumption_overrides=consumption_overrides,
            )
            current_price = await self._current_price(effective_now, price_overrides=prices)
            return PriceImportResult(
                imported_prices=sum(value is not None for value in prices.values()),
                missing_prices=missing_prices,
                imported_costs=imported_costs,
                missing_costs=missing_costs,
                current_price=current_price,
                summary=summary,
                completed_at=datetime.now(UTC),
            )

    async def async_recalculate(
        self,
        start_date: date,
        end_date: date,
        *,
        now: datetime | None = None,
        consumption_overrides: Mapping[datetime, float | None] | None = None,
    ) -> PriceImportResult:
        """Recalculate costs after consumption or pricing options change."""
        if end_date <= start_date:
            raise HehkuApiError("Cost recalculation end_date must be after start_date")
        if (end_date - start_date).days > MAX_BACKFILL_DAYS:
            raise HehkuApiError(f"A cost rebuild may span at most {MAX_BACKFILL_DAYS} days")
        async with self._lock:
            start, end = self._utc_date_range(start_date, end_date)
            prices = await self._read_prices(start, end)
            imported_costs, missing_costs = await self._write_costs(
                prices, consumption_overrides=consumption_overrides
            )
            effective_now = now or datetime.now(UTC)
            return PriceImportResult(
                imported_prices=0,
                missing_prices=0,
                imported_costs=imported_costs,
                missing_costs=missing_costs,
                current_price=await self._current_price(effective_now),
                summary=await self._async_summary(
                    effective_now, consumption_overrides=consumption_overrides
                ),
                completed_at=datetime.now(UTC),
            )

    async def _fetch_prices(
        self, start_date: date, end_date: date
    ) -> tuple[dict[datetime, float | None], int]:
        prices: dict[datetime, float | None] = {}
        missing = 0
        for chunk_start, chunk_end in dst_safe_date_chunks(
            start_date, end_date, self.timezone, max_days=31
        ):
            start = datetime.combine(chunk_start, time.min)
            end = datetime.combine(chunk_end, time.min)
            payload = await self.client.get_market_prices(
                self.location_id, start.isoformat(), end.isoformat()
            )
            if payload.get("resolution") != "hour":
                raise HehkuApiError("Hehku returned unexpected market-price resolution")
            if payload.get("currency") != CURRENCY:
                raise HehkuApiError("Hehku market prices must use EUR")
            response_from = self._parse_boundary(payload.get("from"), "from")
            response_to = self._parse_boundary(payload.get("to"), "to")
            if response_from < start or response_to > end or response_to <= response_from:
                raise HehkuApiError("Hehku returned market prices outside the requested interval")
            values = payload["values"]
            try:
                starts = self._interval_starts(response_from, response_to, len(values))
            except IntervalAlignmentError as err:
                raise HehkuApiError(str(err)) from err
            for interval_start, expected_local, item in zip(
                starts,
                (value.astimezone(self.zone).replace(tzinfo=None) for value in starts),
                values,
                strict=True,
            ):
                if not isinstance(item, dict):
                    raise HehkuApiError("Hehku returned an invalid market-price item")
                period_start = self._parse_boundary(item.get("period_start"), "period_start")
                if period_start != expected_local:
                    raise HehkuApiError("Hehku market-price timestamps are not aligned")
                raw_price = item.get("price_kwh")
                if raw_price is None:
                    prices[interval_start] = None
                    missing += 1
                elif isinstance(raw_price, int | float):
                    prices[interval_start] = float(raw_price)
                else:
                    raise HehkuApiError("Hehku returned a non-numeric market price")
        return prices, missing

    def _interval_starts(self, start: datetime, end: datetime, value_count: int) -> list[datetime]:
        from .intervals import interval_starts

        return interval_starts(start, end, self.timezone, value_count)

    @staticmethod
    def _parse_boundary(value: object, name: str) -> datetime:
        if not isinstance(value, str):
            raise HehkuApiError(f"Hehku market-price response is missing {name}")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as err:
            raise HehkuApiError(f"Hehku returned invalid market-price {name}") from err
        if parsed.tzinfo is not None:
            raise HehkuApiError("Hehku returned timezone-aware market-price timestamps")
        return parsed

    def _write_prices(self, prices: dict[datetime, float | None]) -> None:
        rows = [
            cast(
                StatisticData,
                {
                    "start": start,
                    "state": price,
                    "mean": price,
                    "min": price,
                    "max": price,
                },
            )
            for start, price in sorted(prices.items())
            if price is not None
        ]
        if not rows:
            return
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_mean=True,
            has_sum=False,
            name=f"{self.location_name} spot price",
            source=DOMAIN,
            statistic_id=self.price_statistic_id,
            unit_class=None,
            unit_of_measurement=PRICE_UNIT,
        )
        async_add_external_statistics(self.hass, metadata, rows)

    async def _write_costs(
        self,
        prices: Mapping[datetime, float | None],
        *,
        consumption_overrides: Mapping[datetime, float | None] | None = None,
    ) -> tuple[int, int]:
        if not prices:
            return 0, 0
        first_start = min(prices)
        requested_end = max(prices) + timedelta(hours=1)
        consumption = await self._read_consumption(first_start, requested_end)
        if consumption_overrides:
            for start, value in consumption_overrides.items():
                if first_start <= start < requested_end:
                    if value is None:
                        consumption.pop(start, None)
                    else:
                        consumption[start] = value
        calculated = calculate_costs(
            consumption,
            prices,
            spot_multiplier=self.spot_multiplier,
            margin=self.margin,
        )
        fetched = {
            start: cost.total if isinstance(cost, IntervalCost) else None
            for start, cost in calculated.items()
            if start in prices
        }
        missing = sum(value is None for value in fetched.values())
        if not any(value is not None for value in fetched.values()):
            return 0, missing

        effective_end = await self._effective_cost_end(requested_end)
        if effective_end - first_start > timedelta(days=MAX_BACKFILL_DAYS):
            raise HehkuApiError(
                "Existing cost statistics extend beyond the maximum safe rebuild range"
            )
        prior = await self._latest_cost_before(first_start)
        existing = await self._existing_cost_statistics(first_start, effective_end)
        rows = rebuild_statistics(fetched, existing, base_sum=prior.sum if prior else 0.0)
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_mean=False,
            has_sum=True,
            name=f"{self.location_name} estimated supply cost",
            source=DOMAIN,
            statistic_id=self.cost_statistic_id,
            unit_class=None,
            unit_of_measurement=CURRENCY,
        )
        async_add_external_statistics(
            self.hass, metadata, [cast(StatisticData, row) for row in rows]
        )
        return len(rows), missing

    async def _read_consumption(self, start: datetime, end: datetime) -> dict[datetime, float]:
        return await self._read_state_series(self.consumption_statistic_id, start, end)

    async def _read_prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        result = await self._statistics(start, end, {self.price_statistic_id}, {"mean"})
        prices: dict[datetime, float] = {}
        for item in result.get(self.price_statistic_id, []):
            value = item.get("mean")
            item_start = item.get("start")
            if value is not None and item_start is not None:
                prices[
                    datetime.fromtimestamp(
                        self._numeric(item_start, "price statistic timestamp"), tz=UTC
                    )
                ] = self._numeric(value, "price statistic mean")
        return prices

    async def _read_state_series(
        self, statistic_id: str, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        result = await self._statistics(start, end, {statistic_id}, {"state"})
        values: dict[datetime, float] = {}
        for item in result.get(statistic_id, []):
            value = item.get("state")
            item_start = item.get("start")
            if value is not None and item_start is not None:
                values[
                    datetime.fromtimestamp(self._numeric(item_start, "statistic timestamp"), tz=UTC)
                ] = self._numeric(value, "statistic state")
        return values

    async def _statistics(
        self,
        start: datetime,
        end: datetime,
        statistic_ids: set[str],
        types: set[str],
    ) -> dict[str, list[dict[str, object]]]:
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            statistic_ids,
            "hour",
            None,
            types,
        )
        return cast(dict[str, list[dict[str, object]]], result)

    async def _effective_cost_end(self, requested_end: datetime) -> datetime:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            self.cost_statistic_id,
            True,
            {"state", "sum"},
        )
        records = last.get(self.cost_statistic_id) if last else None
        if not records:
            return requested_end
        latest = datetime.fromtimestamp(
            self._numeric(records[0]["start"], "last cost timestamp"), tz=UTC
        )
        return max(requested_end, latest + timedelta(hours=1))

    async def _existing_cost_statistics(
        self, start: datetime, end: datetime
    ) -> dict[datetime, ExistingStatistic]:
        result = await self._statistics(start, end, {self.cost_statistic_id}, {"state", "sum"})
        rows: dict[datetime, ExistingStatistic] = {}
        for item in result.get(self.cost_statistic_id, []):
            state = item.get("state")
            cumulative_sum = item.get("sum")
            if state is None or cumulative_sum is None:
                continue
            item_start = datetime.fromtimestamp(
                self._numeric(item["start"], "cost statistic timestamp"), tz=UTC
            )
            rows[item_start] = ExistingStatistic(
                self._numeric(state, "cost statistic state"),
                self._numeric(cumulative_sum, "cost statistic sum"),
            )
        return rows

    async def _latest_cost_before(self, start: datetime) -> ExistingStatistic | None:
        rows = await self._existing_cost_statistics(_STATISTICS_EPOCH, start)
        return rows[max(rows)] if rows else None

    async def _async_summary(
        self,
        now: datetime,
        *,
        price_overrides: Mapping[datetime, float | None] | None = None,
        consumption_overrides: Mapping[datetime, float | None] | None = None,
    ) -> CostSummary:
        local_now = now.astimezone(self.zone)
        month_start = local_now.date().replace(day=1)
        start, end = self._utc_date_range(month_start, local_now.date() + timedelta(days=1))
        consumption = await self._read_consumption(start, end)
        prices = await self._read_prices(start, end)
        if consumption_overrides:
            for interval_start, value in consumption_overrides.items():
                if start <= interval_start < end:
                    if value is None:
                        consumption.pop(interval_start, None)
                    else:
                        consumption[interval_start] = value
        if price_overrides:
            for interval_start, value in price_overrides.items():
                if start <= interval_start < end:
                    if value is None:
                        prices.pop(interval_start, None)
                    else:
                        prices[interval_start] = value
        return summarize_costs(
            calculate_costs(
                consumption,
                prices,
                spot_multiplier=self.spot_multiplier,
                margin=self.margin,
            ),
            timezone=self.zone,
            now=now,
        )

    async def _current_price(
        self,
        now: datetime,
        *,
        price_overrides: Mapping[datetime, float | None] | None = None,
    ) -> float | None:
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        if price_overrides and hour_start in price_overrides:
            return price_overrides[hour_start]
        prices = await self._read_prices(hour_start, hour_start + timedelta(hours=1))
        return prices.get(hour_start)

    async def async_day_complete(self, day: date) -> bool:
        """Return whether every local hour has a stored market price."""
        start, end = self._utc_date_range(day, day + timedelta(days=1))
        prices = await self._read_prices(start, end)
        return len(prices) == expected_interval_count(day, self.timezone)

    @staticmethod
    def _numeric(value: object, name: str) -> float:
        if not isinstance(value, int | float):
            raise HehkuApiError(f"Hehku recorder contains invalid {name}")
        return float(value)

    def _utc_date_range(self, start_date: date, end_date: date) -> tuple[datetime, datetime]:
        start = datetime.combine(start_date, time.min).replace(tzinfo=self.zone).astimezone(UTC)
        end = datetime.combine(end_date, time.min).replace(tzinfo=self.zone).astimezone(UTC)
        return start, end
