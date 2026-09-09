"""Button platform for Hehku Energia."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HehkuCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the manual recent-consumption refresh button."""
    del hass
    async_add_entities([HehkuRefreshButton(entry, entry.runtime_data)])


class HehkuRefreshButton(CoordinatorEntity[HehkuCoordinator], ButtonEntity):
    """Trigger the same trailing-window import used by hourly polling."""

    _attr_has_entity_name = True
    _attr_translation_key = "fetch_recent"
    _attr_icon = "mdi:database-refresh"

    def __init__(self, entry: ConfigEntry, coordinator: HehkuCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_fetch_recent"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Hehku Energia / Eliq",
            model=NAME,
        )

    async def async_press(self) -> None:
        """Fetch and import the trailing correction window now."""
        await self.coordinator.async_request_refresh()
