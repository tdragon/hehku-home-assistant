"""Tests for statistics rebuilding."""

from datetime import UTC, datetime, timedelta

from custom_components.hehku_energy.statistics_core import ExistingStatistic, rebuild_statistics

START = datetime(2026, 8, 1, tzinfo=UTC)


def test_builds_cumulative_kwh_sum() -> None:
    fetched = {
        START: 0.5,
        START + timedelta(hours=1): 0.25,
    }

    result = rebuild_statistics(fetched, {}, base_sum=10.0)

    assert result == [
        {"start": START, "state": 0.5, "sum": 10.5},
        {"start": START + timedelta(hours=1), "state": 0.25, "sum": 10.75},
    ]


def test_correction_recalculates_following_sums() -> None:
    second = START + timedelta(hours=1)
    existing = {
        START: ExistingStatistic(state=0.5, sum=10.5),
        second: ExistingStatistic(state=0.25, sum=10.75),
    }

    result = rebuild_statistics({START: 0.7, second: 0.25}, existing, base_sum=10.0)

    assert [item["sum"] for item in result] == [10.7, 10.95]


def test_null_does_not_shift_positions_and_preserves_existing_value() -> None:
    second = START + timedelta(hours=1)
    existing = {second: ExistingStatistic(state=0.25, sum=10.75)}

    result = rebuild_statistics({START: None, second: None}, existing, base_sum=10.5)

    assert result == [{"start": second, "state": 0.25, "sum": 10.75}]
