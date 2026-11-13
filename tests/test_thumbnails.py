"""Tests for thumbnails in the media browser, the view and the service."""

import asyncio
import datetime as dt
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qsl, urlencode

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from reolink_aio.typings import VOD_trigger

from custom_components.reolink_thumbs import (
    DATA_GENERATOR,
    _async_generate_camera_files,
    async_setup_entry,
)
from custom_components.reolink_thumbs.const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DOMAIN,
)
from custom_components.reolink_thumbs.thumbnail import (
    generate_thumbnail,
    get_normalized_thumb_path,
)

TZ = dt.timezone(dt.timedelta(hours=1))
FILE_NAME = "Mp4Record/2026-11-08/RecM01_20261108_120000_120100_0_1.mp4"
THUMB = Path(".cache/reolink_thumbs/Mp4Record/2026-11-08/Rec01_20261108_120100.png")
LEGACY_THUMB = Path("www/recordings/Mp4Record/2026-11-08/Rec01_20261108_120100.png")
VOD_URL = "rtmp://camera/vod"
ENTRY_ID = "cam1"


def _vod_file(
    triggers: VOD_trigger = VOD_trigger.NONE, file_name: str = FILE_NAME
) -> SimpleNamespace:
    start = dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=TZ)
    end = dt.datetime(2026, 11, 8, 12, 1, 0, tzinfo=TZ)
    return SimpleNamespace(
        file_name=file_name,
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


def _fake_generate(link: str, path: Path, image: bool = False) -> bool:
    path.write_bytes(f"{link} {image}".encode())
    return True


@pytest.fixture
def generate(host: MagicMock) -> Iterator[MagicMock]:
    """Mock the Reolink host lookup and FFmpeg in the thumbnail generator."""
    with (
        patch("custom_components.reolink_thumbs.thumbnail.get_host", return_value=host),
        patch(
            "custom_components.reolink_thumbs.thumbnail.generate_thumbnail",
            side_effect=_fake_generate,
        ) as generate,
    ):
        yield generate


@pytest.fixture
async def setup(hass: HomeAssistant, config_dir: Path) -> MockConfigEntry:
    """Set up HTTP, a Reolink entry and the integration."""
    assert await async_setup_component(hass, "http", {})
    MockConfigEntry(domain="reolink", entry_id=ENTRY_ID, data={}).add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await async_setup_entry(hass, entry)
    return entry


def _url(
    hass: HomeAssistant, file: SimpleNamespace | None = None, entry_id: str = ENTRY_ID
) -> str:
    """Return a signed thumbnail URL as listed by the media browser."""
    return hass.data[DATA_GENERATOR].url(entry_id, 0, file or _vod_file())


def _tamper(url: str, **changes: str) -> str:
    """Return the URL with changed query parameters, keeping the signature."""
    path, _, query = url.partition("?")
    return f"{path}?{urlencode(dict(parse_qsl(query)) | changes)}"


async def test_browse_does_not_wait_for_thumbnails(
    hass: HomeAssistant, setup: MockConfigEntry, host: MagicMock
) -> None:
    """Test the media browser links the view without creating thumbnails."""
    with (
        patch("custom_components.reolink_thumbs.get_host", return_value=host),
        patch(
            "custom_components.reolink_thumbs.thumbnail.generate_thumbnail"
        ) as generate,
    ):
        result = await _async_generate_camera_files(
            SimpleNamespace(hass=hass), "entry", 0, "main", 2026, 11, 8
        )
    generate.assert_not_called()
    host.api.get_vod_source.assert_not_called()
    path, _, query = result.children[0].thumbnail.partition("?")
    params = dict(parse_qsl(query))
    assert path == f"/api/{DOMAIN}/entry/0"
    assert params["file"] == FILE_NAME
    assert params["start"] == "2026-11-08T12:00:00+01:00"
    assert params["end"] == "2026-11-08T12:01:00+01:00"
    assert len(params["sig"]) == 64
    assert result.title == "Front Door High res. 2026/11/8"


async def test_browse_nvr_event_folders(
    hass: HomeAssistant, setup: MockConfigEntry, host: MagicMock
) -> None:
    """Test NVR trigger folders and dual lens titles."""
    host.api.is_nvr = True
    host.api.is_dual_lens = True
    host.api.request_vod_files.return_value = (None, [_vod_file(VOD_trigger.PERSON)])
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
    assert result.title == "Front Door lens 0 Low res. 2026/11/8"
    assert event.title == "Front Door lens 0 Low res. 2026/11/8 Person"


async def test_view_creates_and_caches_thumbnail(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup: MockConfigEntry,
    config_dir: Path,
    generate: MagicMock,
) -> None:
    """Test the view creates a thumbnail once, also for concurrent requests."""
    client = await hass_client()
    first, second = await asyncio.gather(client.get(_url(hass)), client.get(_url(hass)))
    for response in (first, second):
        assert response.status == 200
        assert await response.read() == f"{VOD_URL} False".encode()
    assert "immutable" in first.headers["Cache-Control"]
    assert (config_dir / THUMB).exists()

    # A cached thumbnail is served without FFmpeg
    response = await client.get(_url(hass))
    assert response.status == 200
    assert generate.call_count == 1


async def test_view_moves_legacy_thumbnail(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup: MockConfigEntry,
    config_dir: Path,
    generate: MagicMock,
) -> None:
    """Test a thumbnail of an earlier version is moved out of www."""
    legacy = config_dir / LEGACY_THUMB
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    response = await (await hass_client()).get(_url(hass))
    assert await response.read() == b"legacy"
    assert not legacy.exists()
    assert (config_dir / THUMB).read_bytes() == b"legacy"
    generate.assert_not_called()


async def test_view_uses_external_snapshot(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup: MockConfigEntry,
    config_dir: Path,
    host: MagicMock,
    generate: MagicMock,
) -> None:
    """Test the view prefers a matching external snapshot."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    snapshot = config_dir / "snapshots" / "Snapshot_20261108_120030_person.jpg"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"")
    hass.config_entries.async_update_entry(
        setup,
        options={
            CONF_SNAPSHOT_DIRECTORY: str(snapshot.parent),
            CONF_SNAPSHOT_PATTERN: "Snapshot_%Y%m%d_%H%M%S_person*",
            CONF_SNAPSHOT_TOLERANCE: 0,
        },
    )
    response = await (await hass_client()).get(_url(hass))
    assert await response.read() == f"{snapshot} True".encode()
    host.api.get_vod_source.assert_not_called()


LONG = SimpleNamespace(
    file_name=FILE_NAME,
    start_time=dt.datetime(1, 1, 2, tzinfo=dt.UTC),
    end_time=dt.datetime(9999, 12, 31, tzinfo=dt.UTC),
)
REVERSED = SimpleNamespace(
    file_name=FILE_NAME,
    start_time=_vod_file().end_time,
    end_time=_vod_file().start_time,
)


@pytest.mark.parametrize(
    ("make_url", "status"),
    [
        (lambda hass: _url(hass, entry_id="unknown"), 404),
        (lambda hass: _url(hass).partition("&sig=")[0], 400),
        (lambda hass: _tamper(_url(hass), sig="é"), 400),
        # The window must not be widened without a valid signature
        (lambda hass: _tamper(_url(hass), start="0001-01-02"), 400),
        (lambda hass: _url(hass).replace(f"{ENTRY_ID}/0?", f"{ENTRY_ID}/1?"), 400),
        (lambda hass: _url(hass, LONG), 400),
        (lambda hass: _url(hass, REVERSED), 400),
        (
            lambda hass: _url(
                hass, _vod_file(file_name="Mp4Record/../../../secrets.yaml")
            ),
            400,
        ),
    ],
)
async def test_view_invalid_requests(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup: MockConfigEntry,
    host: MagicMock,
    generate: MagicMock,
    make_url: Callable[[HomeAssistant], str],
    status: int,
) -> None:
    """Test invalid requests are rejected without touching the device."""
    response = await (await hass_client()).get(make_url(hass))
    assert response.status == status
    generate.assert_not_called()
    host.api.get_vod_source.assert_not_called()


async def test_view_requires_authentication(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    setup: MockConfigEntry,
    generate: MagicMock,
) -> None:
    """Test thumbnails are not served to anonymous clients."""
    response = await (await hass_client_no_auth()).get(_url(hass))
    assert response.status == 401
    generate.assert_not_called()


async def test_view_generation_fails(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup: MockConfigEntry,
    host: MagicMock,
    generate: MagicMock,
) -> None:
    """Test a recording without a thumbnail returns 404."""
    host.api.get_vod_source.side_effect = RuntimeError("offline")
    response = await (await hass_client()).get(_url(hass))
    assert response.status == 404
    generate.assert_not_called()

    host.api.get_vod_source.side_effect = None
    generate.side_effect = lambda *args: False
    response = await (await hass_client()).get(_url(hass))
    assert response.status == 404


async def test_parallel_recordings_are_limited(
    hass: HomeAssistant, setup: MockConfigEntry, host: MagicMock
) -> None:
    """Test at most three recordings per device are read at the same time."""
    files = [
        _vod_file(file_name=f"Mp4Record/2026-11-08/RecM01_20261108_12000{i}_12010{i}")
        for i in range(8)
    ]
    host.api.request_vod_files.return_value = (None, files)
    lock = threading.Lock()
    running = 0
    peak = 0

    def slow_generate(link: str, path: Path, image: bool = False) -> bool:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return _fake_generate(link, path, image)

    with (
        patch("custom_components.reolink_thumbs.get_host", return_value=host),
        patch("custom_components.reolink_thumbs.thumbnail.get_host", return_value=host),
        patch(
            "custom_components.reolink_thumbs.thumbnail.generate_thumbnail",
            side_effect=slow_generate,
        ) as generate,
    ):
        await hass.services.async_call(
            DOMAIN, "generate_thumbnails", {"entry": ENTRY_ID}, blocking=True
        )
    assert generate.call_count == 8
    assert 1 < peak <= 3


async def test_service(
    hass: HomeAssistant,
    setup: MockConfigEntry,
    config_dir: Path,
    host: MagicMock,
    generate: MagicMock,
) -> None:
    """Test the service creates missing thumbnails for all Reolink entries."""
    MockConfigEntry(domain="reolink", entry_id="cam2", data={}).add_to_hass(hass)
    assert await async_setup_entry(hass, setup)

    with patch(
        "custom_components.reolink_thumbs.get_host",
        side_effect=[host, AttributeError("has no attribute 'runtime_data'")],
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
    setup: MockConfigEntry,
    host: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    level: str,
) -> None:
    """Test errors while listing recordings are logged and skipped."""
    host.api.request_vod_files.side_effect = error
    caplog.set_level("DEBUG")
    with patch("custom_components.reolink_thumbs.get_host", return_value=host):
        await hass.services.async_call(
            DOMAIN, "generate_thumbnails", {"entry": ENTRY_ID}, blocking=True
        )
    assert any(
        record.levelname == level and "channel 0" in record.getMessage()
        for record in caplog.records
    )


async def test_service_other_errors(
    hass: HomeAssistant,
    config_dir: Path,
    host: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test failing sources, file names and devices do not abort the service."""
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await async_setup_entry(hass, entry)
    # No Reolink entries
    await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)

    MockConfigEntry(domain="reolink", entry_id="cam1", data={}).add_to_hass(hass)
    MockConfigEntry(domain="reolink", entry_id="cam2", data={}).add_to_hass(hass)
    host.api.get_vod_source.side_effect = RuntimeError("offline")
    host.api.request_vod_files.return_value = (
        None,
        [_vod_file(), _vod_file(file_name="../escape.mp4")],
    )
    with (
        patch(
            "custom_components.reolink_thumbs.get_host",
            side_effect=[host, RuntimeError("broken")],
        ),
        patch("custom_components.reolink_thumbs.thumbnail.get_host", return_value=host),
    ):
        await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)
    assert "Failed to generate thumbnail for ../escape.mp4" in caplog.text
    with patch(
        "custom_components.reolink_thumbs._generate_missing_thumbnails",
        side_effect=RuntimeError("unexpected"),
    ):
        await hass.services.async_call(DOMAIN, "generate_thumbnails", {}, blocking=True)
    assert not (config_dir / THUMB).exists()


def test_normalized_thumb_path() -> None:
    """Test main and sub streams and absolute paths share one thumbnail."""
    base = Path("/cache")
    main = get_normalized_thumb_path(base, FILE_NAME)
    assert main == base / "Mp4Record/2026-11-08/Rec01_20261108_120100.png"
    assert get_normalized_thumb_path(base, FILE_NAME.replace("RecM", "RecS")) == main
    assert get_normalized_thumb_path(base, "/mnt/sda/RecM02_x.mp4") == Path(
        "/cache/mnt/sda/Rec02_x.png"
    )
    with pytest.raises(ValueError):
        get_normalized_thumb_path(base, "a/../../b.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_generate_thumbnail_with_ffmpeg(tmp_path: Path) -> None:
    """Test images and videos are scaled and stored as PNG."""
    source = tmp_path / "snapshot.jpg"
    Image.new("RGB", (640, 640), "red").save(source)
    target = tmp_path / "thumb.png"
    assert generate_thumbnail(str(source), target, image=True)
    with Image.open(target) as image:
        assert image.format == "PNG"
        assert image.size == (256, 256)

    video = tmp_path / "recording.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=512x256:rate=10",
            "-t",
            "2",
            str(video),
        ],
        check=True,
    )
    assert generate_thumbnail(str(video), tmp_path / "video.png")
    with Image.open(tmp_path / "video.png") as image:
        assert image.size == (256, 128)

    missing = tmp_path / "missing.png"
    assert not generate_thumbnail(str(tmp_path / "missing.jpg"), missing, image=True)
    assert not missing.exists()
    assert list(tmp_path.glob("*.tmp.png")) == []


def _mock_process(returncode: int = 0, communicate=None) -> MagicMock:
    process = MagicMock(returncode=returncode)
    process.communicate.side_effect = communicate or [(b"", b"")]
    return process


def _run_async(ffmpeg_input: MagicMock) -> MagicMock:
    """Return the mocked run_async method of the FFmpeg command chain."""
    output = ffmpeg_input.return_value.filter.return_value.output.return_value
    return output.overwrite_output.return_value.run_async


def test_generate_thumbnail_without_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test a warning is logged if FFmpeg succeeds without writing a file."""
    with patch("custom_components.reolink_thumbs.thumbnail.ffmpeg.input") as inp:
        _run_async(inp).return_value = _mock_process()
        assert not generate_thumbnail("input.jpg", tmp_path / "thumb.png", True)
    inp.assert_called_once_with("input.jpg")
    assert "FFmpeg did not create thumbnail" in caplog.text


def test_generate_thumbnail_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test a hanging FFmpeg process is killed and leaves no file."""
    target = tmp_path / "thumb.png"
    # Output written by FFmpeg before it hung
    target.with_name("thumb.tmp.png").write_bytes(b"partial")
    process = _mock_process(
        communicate=[subprocess.TimeoutExpired("ffmpeg", 30), (b"", b"")]
    )
    with patch("custom_components.reolink_thumbs.thumbnail.ffmpeg.input") as inp:
        _run_async(inp).return_value = process
        assert not generate_thumbnail(VOD_URL, target)
    process.kill.assert_called_once()
    assert "did not finish" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_generate_thumbnail_unexpected_error(tmp_path: Path) -> None:
    """Test unexpected errors are logged and not raised."""
    with patch(
        "custom_components.reolink_thumbs.thumbnail.ffmpeg.input",
        side_effect=OSError("boom"),
    ):
        assert not generate_thumbnail("input", tmp_path / "thumb.png")
