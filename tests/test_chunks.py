"""Tests for chunking consumption requests."""

from datetime import date

from custom_components.hehku_energy.intervals import date_chunks, dst_safe_date_chunks


def test_long_backfill_is_split_below_api_limit() -> None:
    chunks = list(date_chunks(date(2026, 1, 1), date(2026, 8, 1), max_days=92))

    assert chunks[0] == (date(2026, 1, 1), date(2026, 4, 3))
    assert chunks[-1][1] == date(2026, 8, 1)
    assert all((end - start).days <= 92 for start, end in chunks)
    assert all(chunks[index][1] == chunks[index + 1][0] for index in range(len(chunks) - 1))


def test_dst_days_are_isolated_from_regular_chunks() -> None:
    chunks = list(
        dst_safe_date_chunks(date(2026, 3, 20), date(2026, 4, 5), "Europe/Helsinki", max_days=92)
    )

    assert chunks == [
        (date(2026, 3, 20), date(2026, 3, 29)),
        (date(2026, 3, 29), date(2026, 3, 30)),
        (date(2026, 3, 30), date(2026, 4, 5)),
    ]
