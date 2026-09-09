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


def _utc_boundary_candidates(value: datetime, zone: ZoneInfo) -> list[datetime]:
    """Return real UTC instants represented by a naive local boundary."""
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        round_trip = candidate.astimezone(zone)
        if round_trip.replace(tzinfo=None) == value and round_trip.fold == fold:
            candidates.append(candidate)
    return candidates


def interval_starts(
    start: datetime,
    end: datetime,
    timezone: str,
    value_count: int,
) -> list[datetime]:
    """Map a local half-open interval to real UTC hourly starts.

    Iterating in UTC naturally skips the nonexistent spring hour and represents both
    folds of the repeated autumn hour. For an ambiguous clipped boundary, the returned
    value count selects the only possible fold. Other mismatches are rejected.
    """
    if start.tzinfo is not None or end.tzinfo is not None:
        raise IntervalAlignmentError("Eliq request boundaries must be naive local datetimes")
    if any((value.minute, value.second, value.microsecond) != (0, 0, 0) for value in (start, end)):
        raise IntervalAlignmentError("Eliq response boundaries must be whole hours")
    if end <= start:
        raise IntervalAlignmentError("End must be after start")

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as err:
        raise IntervalAlignmentError(f"Unknown timezone: {timezone}") from err

    possible: set[tuple[datetime, ...]] = set()
    for start_utc in _utc_boundary_candidates(start, zone):
        for end_utc in _utc_boundary_candidates(end, zone):
            if end_utc <= start_utc:
                continue
            duration = end_utc - start_utc
            if duration % timedelta(hours=1):
                continue
            possible.add(
                tuple(
                    start_utc + timedelta(hours=offset)
                    for offset in range(int(duration / timedelta(hours=1)))
                )
            )

    matching = [starts for starts in possible if len(starts) == value_count]
    if len(matching) == 1:
        return list(matching[0])

    expected = sorted({len(starts) for starts in possible})
    expected_text = (
        " or ".join(str(count) for count in expected) if expected else "a valid number of"
    )
    raise IntervalAlignmentError(
        f"Eliq returned {value_count} values; expected {expected_text} for "
        f"{start.isoformat()} to {end.isoformat()} in {timezone}. "
        "The data was not imported because DST alignment is unknown."
    )


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
