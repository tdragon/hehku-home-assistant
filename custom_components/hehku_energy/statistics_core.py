"""Pure cumulative-statistics reconstruction helpers."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class ExistingStatistic:
    """Existing state and cumulative sum at an hourly timestamp."""

    state: float
    sum: float


class StatisticRow(TypedDict):
    """Recorder-compatible statistic row."""

    start: datetime
    state: float
    sum: float


def rebuild_statistics(
    fetched: dict[datetime, float | None],
    existing: dict[datetime, ExistingStatistic],
    *,
    base_sum: float,
) -> list[StatisticRow]:
    """Merge fetched values with existing rows and rebuild all following sums.

    A fetched non-null reading wins. A null preserves an existing reading at that
    exact timestamp, if one exists. Missing timestamps never shift later readings.
    """
    total = float(base_sum)
    result: list[StatisticRow] = []

    for start in sorted(fetched.keys() | existing.keys()):
        value = fetched.get(start)
        if value is None:
            old = existing.get(start)
            if old is None:
                continue
            value = old.state
        total += float(value)
        result.append({"start": start, "state": float(value), "sum": total})

    return result
