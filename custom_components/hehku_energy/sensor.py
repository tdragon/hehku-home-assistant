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
        return self.coordinator.data.latest_kwh

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        start = self.coordinator.data.latest_start
        return {"interval_start": start.isoformat()} if start else None


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
        return self.coordinator.data.completed_at

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {
            "imported_rows": self.coordinator.data.imported,
            "missing_intervals": self.coordinator.data.missing,
        }
