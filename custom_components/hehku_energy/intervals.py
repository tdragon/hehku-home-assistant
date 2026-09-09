"""Timezone-safe utilities for Eliq positional hourly values."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class IntervalAlignmentError(ValueError):
    """Raised when positional API values cannot be mapped safely."""


def local_midnight(value: date, timezone: str) -> datetime:
    """Return a naive local midnight for an Eliq request."""
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as err:
        raise IntervalAlignmentError(f"Unknown timezone: {timezone}") from err
    return datetime.combine(value, time.min)


def interval_starts(
    start: datetime,
    end: datetime,
    timezone: str,
    value_count: int,
) -> list[datetime]:
    """Map a local half-open interval to real UTC hourly starts.

    Iterating in UTC naturally skips the nonexistent spring hour and represents both
    folds of the repeated autumn hour. A count mismatch is rejected rather than
    guessing how an undocumented API treats DST.
    """
    if start.tzinfo is not None or end.tzinfo is not None:
        raise IntervalAlignmentError("Eliq request boundaries must be naive local datetimes")
    if end <= start:
        raise IntervalAlignmentError("End must be after start")

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as err:
        raise IntervalAlignmentError(f"Unknown timezone: {timezone}") from err

    cursor = start.replace(tzinfo=zone).astimezone(UTC)
    end_utc = end.replace(tzinfo=zone).astimezone(UTC)
    starts: list[datetime] = []
    while cursor < end_utc:
        starts.append(cursor)
        cursor += timedelta(hours=1)

    if len(starts) != value_count:
        raise IntervalAlignmentError(
            f"Eliq returned {value_count} values; expected {len(starts)} for "
            f"{start.isoformat()} to {end.isoformat()} in {timezone}. "
            "The data was not imported because DST alignment is unknown."
        )
    return starts


def expected_interval_count(start_date: date, timezone: str) -> int:
    """Return the number of real hours in one local calendar day."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(start_date, time.min).replace(tzinfo=zone).astimezone(UTC)
    end = (
        datetime.combine(start_date + timedelta(days=1), time.min)
        .replace(tzinfo=zone)
        .astimezone(UTC)
    )
    return int((end - start) / timedelta(hours=1))


def dst_safe_date_chunks(
    start: date,
    end: date,
    timezone: str,
    *,
    max_days: int = 92,
) -> Iterator[tuple[date, date]]:
    """Chunk a range while isolating 23/25-hour dates into one-day requests."""
    if end <= start:
        raise ValueError("End date must be after start date")
    segment_start = start
    cursor = start
    while cursor < end:
        if expected_interval_count(cursor, timezone) != 24:
            if segment_start < cursor:
                yield from date_chunks(segment_start, cursor, max_days=max_days)
            transition_end = cursor + timedelta(days=1)
            yield cursor, transition_end
            segment_start = transition_end
        cursor += timedelta(days=1)
    if segment_start < end:
        yield from date_chunks(segment_start, end, max_days=max_days)


def date_chunks(start: date, end: date, *, max_days: int = 92) -> Iterator[tuple[date, date]]:
    """Split a half-open date range into contiguous API-safe chunks."""
    if end <= start:
        raise ValueError("End date must be after start date")
    if max_days < 1:
        raise ValueError("max_days must be positive")

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=max_days), end)
        yield cursor, chunk_end
        cursor = chunk_end
