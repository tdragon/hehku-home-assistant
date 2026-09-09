"""Home Assistant integration and configuration-flow tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hehku_energy import async_setup, async_setup_entry
from custom_components.hehku_energy.api import Credentials, LoginTicket
from custom_components.hehku_energy.const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_UUID,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_REFRESH_TOKEN,
    CONF_TIME_ZONE,
    CONF_USER_ID,
    DOMAIN,
    SERVICE_BACKFILL,
)
from custom_components.hehku_energy.statistics import ImportResult


async def test_registers_backfill_action(hass: HomeAssistant) -> None:
    """The integration registers its parameterized historical action."""
    assert await async_setup(hass, {})
    assert hass.services.has_service(DOMAIN, SERVICE_BACKFILL)


async def test_magic_link_flow_creates_entry(hass: HomeAssistant) -> None:
    """A completed magic-link ticket creates an entry for the selected location."""
    credentials = Credentials("7", "access", "refresh", "device-uuid")

    with (
        patch("homeassistant.config_entries.async_process_deps_reqs", AsyncMock()),
        patch.object(hass.config_entries, "async_setup", AsyncMock(return_value=True)),
        patch(
            "custom_components.hehku_energy.config_flow.HehkuApi.request_magic_link",
            AsyncMock(return_value=LoginTicket("ticket", "completed")),
        ),
        patch(
            "custom_components.hehku_energy.config_flow.HehkuApi.exchange_ticket",
            AsyncMock(return_value=credentials),
        ),
        patch(
            "custom_components.hehku_energy.config_flow.HehkuApi.get_locations",
            AsyncMock(return_value=[{"id": 42, "name": "Apartment"}]),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "owner@example.invalid"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Apartment"
    assert result["data"]["location_id"] == 42
    assert result["data"]["refresh_token"] == "refresh"


async def test_setup_entry_polls_and_persists_rotated_credentials(
    hass: HomeAssistant,
) -> None:
    """Entry setup performs a real coordinator refresh and persists rotation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Apartment",
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        data={
            CONF_USER_ID: "7",
            CONF_ACCESS_TOKEN: "access",
            CONF_REFRESH_TOKEN: "refresh",
            CONF_DEVICE_UUID: "device-uuid",
            CONF_LOCATION_ID: 42,
            CONF_LOCATION_NAME: "Apartment",
            CONF_TIME_ZONE: "Europe/Helsinki",
        },
    )
    entry.add_to_hass(hass)
    initial_result = ImportResult(
        imported=1,
        missing=0,
        latest_start=datetime(2026, 9, 9, 20, tzinfo=UTC),
        latest_kwh=0.25,
        completed_at=datetime(2026, 9, 9, 21, tzinfo=UTC),
    )

    with (
        patch(
            "custom_components.hehku_energy.HehkuStatisticsImporter.async_import",
            AsyncMock(return_value=initial_result),
        ) as import_data,
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
    ):
        assert await async_setup(hass, {})
        assert await async_setup_entry(hass, entry)

    import_data.assert_awaited_once()
    coordinator = entry.runtime_data
    assert coordinator.data == initial_result

    coordinator.importer.client._credentials_updated(
        Credentials("7", "new-access", "new-refresh", "device-uuid")
    )
    assert entry.data[CONF_ACCESS_TOKEN] == "new-access"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
