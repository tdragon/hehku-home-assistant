"""Import Eliq interval values into Home Assistant external statistics."""

from __future__ import annotations

import asyncio
import logging
import re
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
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .api import HehkuApiError, HehkuClient
from .const import DOMAIN, MAX_BACKFILL_DAYS
from .intervals import (
    IntervalAlignmentError,
    dst_safe_date_chunks,
    expected_interval_count,
    interval_starts,
)
from .statistics_core import ExistingStatistic, rebuild_statistics

_LOGGER = logging.getLogger(__name__)
_STATISTICS_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Summary exposed by the coordinator."""

    imported: int
    missing: int
    latest_start: datetime | None
    latest_kwh: float | None
    completed_at: datetime


def statistic_id_for_location(location_id: int | str) -> str:
    """Build a recorder-safe, stable external statistic ID."""
    object_id = re.sub(r"[^a-z0-9_]", "_", str(location_id).lower()).strip("_")
    if not object_id:
        raise HehkuApiError("The location ID cannot form a statistic ID")
    return f"{DOMAIN}:{object_id}_energy_consumption"


class HehkuStatisticsImporter:
    """Fetch, align, merge and import hourly consumption."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: HehkuClient,
        location_id: int | str,
        location_name: str,
        timezone: str,
    ) -> None:
        self.hass = hass
        self.client = client
        self.location_id = location_id
        self.location_name = location_name
        self.timezone = timezone
        self.statistic_id = statistic_id_for_location(location_id)
        self.latest_intervals: dict[datetime, float | None] = {}
        self._lock = asyncio.Lock()

    async def async_import(self, start_date: date, end_date: date) -> ImportResult:
        """Import a half-open local date range, extending through later existing rows.

        Extending is required because correcting/inserting an older delta changes every
        subsequent cumulative sum used by the Energy Dashboard.
        """
        async with self._lock:
            effective_end = await self._effective_end_date(end_date)
            if (effective_end - start_date).days > MAX_BACKFILL_DAYS:
                raise HehkuApiError(
                    f"The effective import may span at most {MAX_BACKFILL_DAYS} days"
                )
            fetched, missing = await self._fetch(start_date, effective_end)
            self.latest_intervals = fetched
            if not fetched:
                return ImportResult(0, missing, None, None, datetime.now(UTC))

            first_start = min(fetched)
            final_end = max(fetched) + timedelta(hours=1)
            prior = await self._latest_statistic_before(first_start)
            base_sum = prior.sum if prior is not None else 0.0
            existing = await self._existing_statistics(first_start, final_end)
            rows = rebuild_statistics(fetched, existing, base_sum=base_sum)

            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_mean=False,
                has_sum=True,
                name=f"{self.location_name} energy consumption",
                source=DOMAIN,
                statistic_id=self.statistic_id,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )
            async_add_external_statistics(
                self.hass,
                metadata,
                [cast(StatisticData, row) for row in rows],
            )
            latest = rows[-1] if rows else None
            return ImportResult(
                imported=len(rows),
                missing=missing,
                latest_start=latest["start"] if latest else None,
                latest_kwh=latest["state"] if latest else None,
                completed_at=datetime.now(UTC),
            )

    async def _effective_end_date(self, requested_end: date) -> date:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            self.statistic_id,
            True,
            {"state", "sum"},
        )
        records = last.get(self.statistic_id) if last else None
        if not records:
            return requested_end
        latest_start = datetime.fromtimestamp(float(records[0]["start"]), tz=UTC)
        day_after_latest = latest_start.astimezone(ZoneInfo(self.timezone)).date() + timedelta(
            days=1
        )
        return max(requested_end, day_after_latest)

    async def _fetch(
        self, start_date: date, end_date: date
    ) -> tuple[dict[datetime, float | None], int]:
        fetched: dict[datetime, float | None] = {}
        missing = 0
        for chunk_start, chunk_end in dst_safe_date_chunks(start_date, end_date, self.timezone):
            start = datetime.combine(chunk_start, time.min)
            end = datetime.combine(chunk_end, time.min)
            payload = await self.client.get_consumption(
                self.location_id, start.isoformat(), end.isoformat()
            )
            if payload.get("resolution") != "hour" or payload.get("fuel") != "elec":
                raise HehkuApiError("Hehku returned unexpected consumption metadata")
            values = payload["consumption"]
            response_from = payload.get("from")
            response_to = payload.get("to")
            if not isinstance(response_from, str) or not isinstance(response_to, str):
                raise HehkuApiError("Hehku response is missing consumption boundaries")
            try:
                response_start = datetime.fromisoformat(response_from)
                response_end = datetime.fromisoformat(response_to)
            except ValueError as err:
                raise HehkuApiError("Hehku returned invalid consumption boundaries") from err
            if response_start.tzinfo is not None or response_end.tzinfo is not None:
                raise HehkuApiError("Hehku returned unexpected timezone-aware boundaries")
            if response_start < start or response_end > end or response_end <= response_start:
                raise HehkuApiError("Hehku returned consumption outside the requested interval")
            try:
                starts = interval_starts(response_start, response_end, self.timezone, len(values))
            except IntervalAlignmentError:
                # A DST date is isolated into its own request. Skip only that day
                # until Eliq's populated 23/25-hour behavior has been observed.
                if (chunk_end - chunk_start).days != 1:
                    raise
                _LOGGER.warning(
                    "Skipped ambiguous DST consumption date %s: Hehku returned %s slots",
                    chunk_start,
                    len(values),
                )
                missing += len(values)
                continue
            for interval_start, raw_value in zip(starts, values, strict=True):
                if raw_value is None:
                    fetched[interval_start] = None
                    missing += 1
                elif isinstance(raw_value, int | float):
                    fetched[interval_start] = float(raw_value) / 1000.0
                else:
                    raise HehkuApiError("Hehku returned a non-numeric consumption value")
        return fetched, missing

    async def _existing_statistics(
        self, start: datetime, end: datetime
    ) -> dict[datetime, ExistingStatistic]:
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {self.statistic_id},
            "hour",
            None,
            {"state", "sum"},
        )
        rows: dict[datetime, ExistingStatistic] = {}
        for item in result.get(self.statistic_id, []):
            state = item.get("state")
            cumulative_sum = item.get("sum")
            if state is None or cumulative_sum is None:
                continue
            item_start = datetime.fromtimestamp(float(item["start"]), tz=UTC)
            rows[item_start] = ExistingStatistic(float(state), float(cumulative_sum))
        return rows

    async def async_day_complete(self, day: date) -> bool:
        """Return whether every local hour has stored non-null consumption."""
        zone = ZoneInfo(self.timezone)
        start = datetime.combine(day, time.min).replace(tzinfo=zone).astimezone(UTC)
        end = (
            datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=zone).astimezone(UTC)
        )
        rows = await self._existing_statistics(start, end)
        return len(rows) == expected_interval_count(day, self.timezone)

    async def _latest_statistic_before(self, start: datetime) -> ExistingStatistic | None:
        """Return the newest cumulative row before start, even across missing hours."""
        rows = await self._existing_statistics(_STATISTICS_EPOCH, start)
        if not rows:
            return None
        return rows[max(rows)]
