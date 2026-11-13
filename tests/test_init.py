"""Tests for choosing the thumbnail source of a recording."""

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reolink_thumbs.const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DOMAIN,
)
from custom_components.reolink_thumbs.thumbnail import (
    async_thumbnail_source as _async_thumbnail_source,
)

VOD_URL = "rtmp://camera/vod"


@pytest.fixture
def host() -> MagicMock:
    """Return a mocked Reolink host with one camera."""
    host = MagicMock()
    host.api.is_nvr = False
    host.api.camera_name.return_value = "Front Door"
    host.api.get_vod_source = AsyncMock(return_value=("video/mp4", VOD_URL))
    return host


@pytest.fixture
def recording(hass: HomeAssistant) -> SimpleNamespace:
    """Return a recording from 12:00:00 to 12:01:00 local time."""
    tz = dt.timezone(dt.timedelta(hours=1))
    return SimpleNamespace(
        file_name="Mp4Record/2026-11-08/RecM01_20261108_120000_120100_0_1.mp4",
        start_time=dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=tz),
        end_time=dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=tz),
    )


def _add_entry(hass: HomeAssistant, options: dict) -> None:
    MockConfigEntry(domain=DOMAIN, title=DOMAIN, data={}, options=options).add_to_hass(
        hass
    )


async def test_recording_without_options(
    hass: HomeAssistant, host: MagicMock, recording: SimpleNamespace
) -> None:
    """Test the recording is used when no snapshot directory is configured."""
    assert await _async_thumbnail_source(hass, host, 0, recording) == (VOD_URL, False)
    _add_entry(hass, {})
    assert await _async_thumbnail_source(hass, host, 0, recording) == (VOD_URL, False)
    host.api.get_vod_source.assert_called_with(
        0, recording.file_name, "sub", host.api.get_vod_source.call_args.args[3]
    )


async def test_external_snapshot(
    hass: HomeAssistant, host: MagicMock, recording: SimpleNamespace, tmp_path: Path
) -> None:
    """Test a matching snapshot is preferred over the recording."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    day = tmp_path / "front_door" / "2026-11-08"
    day.mkdir(parents=True)
    (day / "Snapshot_20261108_115958_person.jpg").write_bytes(b"")
    (day / "Snapshot_20261108_120030_person.jpg").write_bytes(b"")
    _add_entry(
        hass,
        {
            CONF_SNAPSHOT_DIRECTORY: f"{tmp_path}/{{camera_slug}}/{{date:%Y-%m-%d}}",
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d_%H%M%S_person*",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    )
    assert await _async_thumbnail_source(hass, host, 0, recording) == (
        str(day / "Snapshot_20261108_120030_person.jpg"),
        True,
    )
    host.api.get_vod_source.assert_not_called()


async def test_tolerance(
    hass: HomeAssistant, host: MagicMock, recording: SimpleNamespace, tmp_path: Path
) -> None:
    """Test the tolerance extends the recording window."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    (tmp_path / "Snapshot_20261108_115958.jpg").write_bytes(b"")
    _add_entry(
        hass,
        {
            CONF_SNAPSHOT_DIRECTORY: str(tmp_path),
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d_%H%M%S*",
            CONF_SNAPSHOT_TOLERANCE: 5,
        },
    )
    assert await _async_thumbnail_source(hass, host, 0, recording) == (
        str(tmp_path / "Snapshot_20261108_115958.jpg"),
        True,
    )


async def test_no_match_or_invalid_options(
    hass: HomeAssistant, host: MagicMock, recording: SimpleNamespace, tmp_path: Path
) -> None:
    """Test fallback to the recording without a match or with bad options."""
    _add_entry(
        hass,
        {
            CONF_SNAPSHOT_DIRECTORY: str(tmp_path),
            CONF_SNAPSHOT_PATTERN: "invalid",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    )
    assert await _async_thumbnail_source(hass, host, 0, recording) == (VOD_URL, False)
    hass.config_entries.async_update_entry(
        hass.config_entries.async_entries(DOMAIN)[0],
        options={
            CONF_SNAPSHOT_DIRECTORY: str(tmp_path),
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d_%H%M%S*",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    )
    assert await _async_thumbnail_source(hass, host, 0, recording) == (VOD_URL, False)
