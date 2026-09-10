"""Pure interval pricing helpers for Hehku Energia."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class IntervalCost:
    """Variable electricity-supply cost for one metered interval."""

    consumption_kwh: float
    spot: float
    margin: float
    total: float


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Current local-day and month-to-date variable-cost totals."""

    today_spot: float
    today_margin: float
    today_total: float
    today_intervals: int
    month_spot: float
    month_margin: float
    month_total: float
    month_intervals: int


def calculate_costs(
    consumption: Mapping[datetime, float | None],
    prices: Mapping[datetime, float | None],
    *,
    spot_multiplier: float,
    margin: float,
) -> dict[datetime, IntervalCost | None]:
    """Join matching intervals and calculate cost before any aggregation."""
    costs: dict[datetime, IntervalCost | None] = {}
    for start in sorted(consumption.keys() | prices.keys()):
        consumed = consumption.get(start)
        price = prices.get(start)
        if consumed is None or price is None:
            costs[start] = None
            continue
        spot_cost = consumed * price * spot_multiplier
        margin_cost = consumed * margin
        costs[start] = IntervalCost(
            consumption_kwh=consumed,
            spot=spot_cost,
            margin=margin_cost,
            total=spot_cost + margin_cost,
        )
    return costs


def summarize_costs(
    costs: dict[datetime, IntervalCost | None],
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> CostSummary:
    """Aggregate calculated intervals by the current local day and month."""
    local_now = now.astimezone(timezone)
    today_spot = today_margin = month_spot = month_margin = 0.0
    today_intervals = month_intervals = 0

    for start, cost in costs.items():
        if cost is None:
            continue
        local_start = start.astimezone(timezone)
        if (local_start.year, local_start.month) != (local_now.year, local_now.month):
            continue
        month_spot += cost.spot
        month_margin += cost.margin
        month_intervals += 1
        if local_start.date() == local_now.date():
            today_spot += cost.spot
            today_margin += cost.margin
            today_intervals += 1

    return CostSummary(
        today_spot=today_spot,
        today_margin=today_margin,
        today_total=today_spot + today_margin,
        today_intervals=today_intervals,
        month_spot=month_spot,
        month_margin=month_margin,
        month_total=month_spot + month_margin,
        month_intervals=month_intervals,
    )
