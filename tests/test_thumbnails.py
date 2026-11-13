"""Tests for thumbnail generation in the media browser and the service."""

import datetime as dt
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry
from reolink_aio.typings import VOD_trigger

from custom_components.reolink_thumbs import (
    _async_generate_camera_files,
    async_setup_entry,
    generate_thumbnail,
)
from custom_components.reolink_thumbs.const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DOMAIN,
)

TZ = dt.timezone(dt.timedelta(hours=1))
FILE_NAME = "Mp4Record/2026-11-08/RecM01_20261108_120000_120100_0_1.mp4"
THUMB = Path("www/recordings/Mp4Record/2026-11-08/Rec01_20261108_120100.png")
VOD_URL = "rtmp://camera/vod"


def _vod_file(triggers: VOD_trigger = VOD_trigger.NONE) -> SimpleNamespace:
    start = dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=TZ)
    end = dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=TZ)
    return SimpleNamespace(
        file_name=FILE_NAME,
        start_time=start,
        end_time=end,
        duration=end - start,
        triggers=triggers,
        start_time_id="20261108120000",
        end_time_id="20261108120100",
    )


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Use a temporary configuration directory."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def host() -> MagicMock:
    """Return a mocked Reolink host with one camera and one recording."""
    host = MagicMock()
    host.api.is_nvr = False
    host.api.is_hub = False
    host.api.is_dual_lens = False
    host.api.channels = [0]
    host.api.camera_name.return_value = "Front Door"
    host.api.request_vod_files = AsyncMock(return_value=(None, [_vod_file()]))
    host.api.get_vod_source = AsyncMock(return_value=("video/mp4", VOD_URL))
    return host


def _fake_generate(link: str, path: Path, image: bool) -> None:
    path.write_bytes(f"{link} {image}".encode())


async def test_browse_creates_thumbnail(
    hass: HomeAssistant, config_dir: Path, host: MagicMock
) -> None:
    """Test the media browser creates and links a thumbnail."""
    media_source = SimpleNamespace(hass=hass)
    with (
        patch("custom_components.reolink_thumbs.get_host", return_value=host),
        patch(
            "custom_components.reolink_thumbs.generate_thumbnail",
            side_effect=_fake_generate,
        ) as generate,
    ):
        result = await _async_generate_camera_files(
            media_source, "entry", 0, "main", 2026, 11, 8
        )
        assert (config_dir / THUMB).read_bytes() == f"{VOD_URL} False".encode()
        assert result.children[0].thumbnail == f"/local/{THUMB.relative_to('www')}"
        assert result.title == "Front Door High res. 2026/11/8"

        # An existing thumbnail is reused
        await _async_generate_camera_files(
            media_source, "entry", 0, "main", 2026, 11, 8
        )
    assert generate.call_count == 1


async def test_browse_uses_external_snapshot(
    hass: HomeAssistant, config_dir: Path, host: MagicMock
) -> None:
    """Test the media browser prefers a matching external snapshot."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    snapshot = config_dir / "snapshots" / "Snapshot_20261108_120030_person.jpg"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"")
    MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_SNAPSHOT_DIRECTORY: str(snapshot.parent),
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d_%H%M%S_person*",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    ).add_to_hass(hass)
    with (
        patch("custom_components.reolink_thumbs.get_host", return_value=host),
        patch(
            "custom_components.reolink_thumbs.generate_thumbnail",
            side_effect=_fake_generate,
        ),
    ):
        await _async_generate_camera_files(
            SimpleNamespace(hass=hass), "entry", 0, "sub", 2026, 11, 8
        )
    assert (config_dir / THUMB).read_bytes() == f"{snapshot} True".encode()
    host.api.get_vod_source.assert_not_called()


async def test_browse_nvr_event_folders_and_errors(
    hass: HomeAssistant, config_dir: Path, host: MagicMock
) -> None:
    """Test NVR trigger folders, dual lens titles and a failing source."""
    host.api.is_nvr = True
    host.api.is_dual_lens = True
    host.api.request_vod_files.return_value = (None, [_vod_file(VOD_trigger.PERSON)])
    host.api.get_vod_source.side_effect = RuntimeError("offline")
    with patch("custom_components.reolink_thumbs.get_host", return_value=host):
        result = await _async_generate_camera_files(
            SimpleNamespace(hass=hass), "entry", 0, "sub", 2026, 11, 8
        )
        event = await _async_generate_camera_files(
            SimpleNamespace(hass=hass), "entry", 0, "sub", 2026, 11, 8, "PERSON"
        )
    assert [child.title for child in result.children] == [
        "Person",
        "12:00:00 0:01:00 Person",
    ]
    assert result.children[1].thumbnail is None
    assert result.title == "Front Door lens 0 Low res. 2026/11/8"
    assert event.title == "Front Door lens 0 Low res. 2026/11/8 Person"


async def test_service(hass: HomeAssistant, config_dir: Path, host: MagicMock) -> None:
    """Test the service creates missing thumbnails for all Reolink entries."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    MockConfigEntry(domain="reolink", entry_id="cam1", data={}).add_to_hass(hass)
    MockConfigEntry(domain="reolink", entry_id="cam2", data={}).add_to_hass(hass)
    assert await async_setup_entry(hass, entry)
    assert await async_setup_entry(hass, entry)

    with (
        patch(
            "custom_components.reolink_thumbs.get_host",
            side_effect=[host, AttributeError("has no attribute 'runtime_data'")],
        ),
        patch(
            "custom_components.reolink_thumbs.generate_thumbnail",
            side_effect=_fake_generate,
        ) as generate,
    ):
        await hass.services.async_call(
            DOMAIN, "generate_thumbnails", {"days": 2}, blocking=True
        )
    assert (config_dir / THUMB).exists()
    assert generate.call_count == 1
    assert host.api.request_vod_files.call_count == 2


@pytest.mark.parametrize(
    ("error", "level"),
    [
        (RuntimeError("{'rspCode': -17}"), "DEBUG"),
        (RuntimeError("timeout"), "ERROR"),
    ],
)
async def test_service_request_errors(
    hass: HomeAssistant,
    config_dir: Path,
    host: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    level: str,
) -> None:
    """Test errors while listing recordings are logged and skipped."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    MockConfigEntry(domain="reolink", entry_id="cam1", data={}).add_to_hass(hass)
    await async_setup_entry(hass, entry)
    host.api.request_vod_files.side_effect = error
    caplog.set_level("DEBUG")
    with patch("custom_components.reolink_thumbs.get_host", return_value=host):
        await hass.services.async_call(
            DOMAIN, "generate_thumbnails", {"entry": "cam1"}, blocking=True
        )
    assert any(
        record.levelname == level and "channel 0" in record.getMessage()
        for record in caplog.records
    )


async def test_service_other_errors(
    hass: HomeAssistant, config_dir: Path, host: MagicMock
) -> None:
    """Test failing sources and devices do not abort the service."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await async_setup_entry(hass, entry)
    # No Reolink entries
    await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)

    MockConfigEntry(domain="reolink", entry_id="cam1", data={}).add_to_hass(hass)
    MockConfigEntry(domain="reolink", entry_id="cam2", data={}).add_to_hass(hass)
    host.api.get_vod_source.side_effect = RuntimeError("offline")
    with patch(
        "custom_components.reolink_thumbs.get_host",
        side_effect=[host, RuntimeError("broken")],
    ):
        await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)
    with patch(
        "custom_components.reolink_thumbs._generate_missing_thumbnails",
        side_effect=RuntimeError("unexpected"),
    ):
        await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)
    assert not (config_dir / THUMB).exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_generate_thumbnail_from_jpeg(tmp_path: Path) -> None:
    """Test a JPEG snapshot is scaled and stored as PNG."""
    source = tmp_path / "snapshot.jpg"
    Image.new("RGB", (640, 640), "red").save(source)
    target = tmp_path / "thumb.png"
    generate_thumbnail(str(source), target, image=True)
    with Image.open(target) as image:
        assert image.format == "PNG"
        assert image.size == (256, 256)

    missing = tmp_path / "missing.png"
    generate_thumbnail(str(tmp_path / "missing.jpg"), missing, image=True)
    assert not missing.exists()


def test_generate_thumbnail_without_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test a warning is logged if FFmpeg succeeds without writing a file."""
    with patch("custom_components.reolink_thumbs.ffmpeg.input") as ffmpeg_input:
        generate_thumbnail("input.jpg", tmp_path / "thumb.png", image=True)
    ffmpeg_input.assert_called_once_with("input.jpg")
    assert "FFmpeg did not create thumbnail" in caplog.text


def test_generate_thumbnail_unexpected_error(tmp_path: Path) -> None:
    """Test unexpected errors are logged and not raised."""
    with patch(
        "custom_components.reolink_thumbs.ffmpeg.input", side_effect=OSError("boom")
    ):
        generate_thumbnail("input", tmp_path / "thumb.png")
