"""Tests for timezone-aware positional interval reconstruction."""

from datetime import UTC, datetime

import pytest

from custom_components.hehku_energy.intervals import IntervalAlignmentError, interval_starts


def test_normal_finnish_day_maps_to_utc() -> None:
    starts = interval_starts(datetime(2026, 8, 1), datetime(2026, 8, 2), "Europe/Helsinki", 24)

    assert starts[0] == datetime(2026, 7, 31, 21, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 8, 1, 20, tzinfo=UTC)


def test_spring_dst_day_has_23_real_hours() -> None:
    starts = interval_starts(datetime(2026, 3, 29), datetime(2026, 3, 30), "Europe/Helsinki", 23)

    assert len(starts) == 23
    assert starts[0] == datetime(2026, 3, 28, 22, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 3, 29, 20, tzinfo=UTC)


def test_autumn_dst_day_has_two_distinct_repeated_hours() -> None:
    starts = interval_starts(datetime(2026, 10, 25), datetime(2026, 10, 26), "Europe/Helsinki", 25)

    local_starts = [
        value.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Helsinki")) for value in starts
    ]
    repeated = [value for value in local_starts if value.hour == 3]
    assert len(repeated) == 2
    assert {value.fold for value in repeated} == {0, 1}


def test_ambiguous_api_count_is_rejected_on_dst_day() -> None:
    with pytest.raises(IntervalAlignmentError, match="expected 23"):
        interval_starts(datetime(2026, 3, 29), datetime(2026, 3, 30), "Europe/Helsinki", 24)
