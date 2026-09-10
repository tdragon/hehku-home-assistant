"""Diagnostic sensor platform for Hehku Energia."""

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HehkuCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Hehku diagnostic entities."""
    del hass
    coordinator: HehkuCoordinator = entry.runtime_data
    async_add_entities(
        [
            HehkuLatestConsumptionSensor(entry, coordinator),
            HehkuSpotPriceSensor(entry, coordinator),
            HehkuTodaySupplyCostSensor(entry, coordinator),
            HehkuMonthSupplyCostSensor(entry, coordinator),
            HehkuLastImportSensor(entry, coordinator),
        ]
    )


class HehkuEntity(CoordinatorEntity[HehkuCoordinator]):
    """Common Hehku entity."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Hehku Energia / Eliq",
            model=NAME,
        )


class HehkuLatestConsumptionSensor(HehkuEntity, SensorEntity):
    """Latest non-null hourly interval value."""

    _attr_translation_key = "latest_hourly_consumption"
    _attr_icon = "mdi:transmission-tower-import"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_latest_hourly_consumption"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.consumption.latest_kwh

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        start = self.coordinator.data.consumption.latest_start
        return {"interval_start": start.isoformat()} if start else None


class HehkuSpotPriceSensor(HehkuEntity, SensorEntity):
    """Current raw Eliq market price in EUR/kWh."""

    _attr_translation_key = "spot_price"
    _attr_icon = "mdi:currency-eur"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/kWh"

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_spot_price"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.pricing.current_price


class HehkuTodaySupplyCostSensor(HehkuEntity, SensorEntity):
    """Current local day's estimated variable supply cost."""

    _attr_translation_key = "today_supply_cost"
    _attr_icon = "mdi:cash-clock"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "EUR"

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_today_supply_cost"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.pricing.summary.today_total, 4)

    @property
    def extra_state_attributes(self) -> dict[str, float | int]:
        summary = self.coordinator.data.pricing.summary
        return {
            "spot_cost": round(summary.today_spot, 4),
            "margin_cost": round(summary.today_margin, 4),
            "covered_intervals": summary.today_intervals,
        }


class HehkuMonthSupplyCostSensor(HehkuEntity, SensorEntity):
    """Current month-to-date estimated variable supply cost."""

    _attr_translation_key = "month_supply_cost"
    _attr_icon = "mdi:calendar-month"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "EUR"

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_month_supply_cost"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.pricing.summary.month_total, 4)

    @property
    def extra_state_attributes(self) -> dict[str, float | int]:
        summary = self.coordinator.data.pricing.summary
        return {
            "spot_cost": round(summary.month_spot, 4),
            "margin_cost": round(summary.month_margin, 4),
            "covered_intervals": summary.month_intervals,
        }


class HehkuLastImportSensor(HehkuEntity, SensorEntity):
    """Timestamp and counts for the last successful import."""

    _attr_translation_key = "last_import"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_import"

    @property
    def native_value(self) -> datetime:
        """Return the UTC completion time."""
        return max(
            self.coordinator.data.consumption.completed_at,
            self.coordinator.data.pricing.completed_at,
        )

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        consumption = self.coordinator.data.consumption
        pricing = self.coordinator.data.pricing
        return {
            "imported_rows": consumption.imported,
            "missing_intervals": consumption.missing,
            "imported_prices": pricing.imported_prices,
            "missing_prices": pricing.missing_prices,
            "imported_costs": pricing.imported_costs,
            "missing_costs": pricing.missing_costs,
        }
