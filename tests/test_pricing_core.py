"""Tests for interval pricing and local calendar summaries."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.hehku_energy.pricing_core import calculate_costs, summarize_costs


def test_calculates_interval_cost_before_aggregation() -> None:
    starts = [
        datetime(2026, 8, 1, 0, tzinfo=UTC),
        datetime(2026, 8, 1, 1, tzinfo=UTC),
    ]
    consumption = {starts[0]: 1.0, starts[1]: 2.0}
    prices = {starts[0]: 0.10, starts[1]: 0.20}

    costs = calculate_costs(consumption, prices, spot_multiplier=1.0, margin=0.0035)

    assert costs[starts[0]].spot == 0.10
    assert costs[starts[0]].margin == 0.0035
    assert costs[starts[0]].total == pytest.approx(0.1035)
    assert costs[starts[1]].spot == 0.40
    assert costs[starts[1]].margin == 0.007
    assert costs[starts[1]].total == pytest.approx(0.407)


def test_missing_input_stays_missing() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)

    costs = calculate_costs({start: 1.0}, {start: None}, spot_multiplier=1.0, margin=0.0035)

    assert costs[start] is None


def test_summarizes_today_and_month_in_local_timezone() -> None:
    helsinki = ZoneInfo("Europe/Helsinki")
    previous_local_day = datetime(2026, 8, 31, 20, tzinfo=UTC)
    current_local_day = datetime(2026, 8, 31, 21, tzinfo=UTC)
    costs = calculate_costs(
        {previous_local_day: 1.0, current_local_day: 2.0},
        {previous_local_day: 0.10, current_local_day: 0.20},
        spot_multiplier=1.0,
        margin=0.0035,
    )

    summary = summarize_costs(
        costs,
        timezone=helsinki,
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert summary.today_total == pytest.approx(0.407)
    assert summary.month_total == pytest.approx(0.407)
    assert summary.today_spot == 0.4
    assert summary.today_margin == 0.007
    assert summary.month_intervals == 1
