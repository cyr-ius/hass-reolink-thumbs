"""Tests for the Reolink Thumbs config and options flow."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reolink_thumbs.const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DEFAULT_SNAPSHOT_PATTERN,
    DOMAIN,
)


async def test_user_flow(hass: HomeAssistant) -> None:
    """Test that the user flow creates an entry without options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Test validation and saving of the external snapshot options."""
    entry = MockConfigEntry(domain=DOMAIN, title=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SNAPSHOT_DIRECTORY: "snapshots/{unknown}",
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        CONF_SNAPSHOT_DIRECTORY: "invalid_directory",
        CONF_SNAPSHOT_PATTERN: "invalid_pattern",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SNAPSHOT_DIRECTORY: " /media/snapshots/{camera_slug}/{date} ",
            CONF_SNAPSHOT_PATTERN: DEFAULT_SNAPSHOT_PATTERN,
            CONF_SNAPSHOT_TOLERANCE: 5.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SNAPSHOT_DIRECTORY: "/media/snapshots/{camera_slug}/{date}",
        CONF_SNAPSHOT_PATTERN: DEFAULT_SNAPSHOT_PATTERN,
        CONF_SNAPSHOT_TOLERANCE: 5,
    }


async def test_options_flow_disable(hass: HomeAssistant) -> None:
    """Test that an empty directory disables external snapshots."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=DOMAIN,
        data={},
        options={CONF_SNAPSHOT_DIRECTORY: "/media/snapshots"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SNAPSHOT_PATTERN: DEFAULT_SNAPSHOT_PATTERN, CONF_SNAPSHOT_TOLERANCE: 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SNAPSHOT_DIRECTORY] == ""
