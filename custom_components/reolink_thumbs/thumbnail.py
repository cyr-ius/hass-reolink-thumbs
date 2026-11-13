"""Create and cache thumbnails of Reolink recordings."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlencode

import ffmpeg
from homeassistant.components.reolink.util import get_host
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from reolink_aio.enums import VodRequestType

from .const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DEFAULT_SNAPSHOT_PATTERN,
    DEFAULT_SNAPSHOT_TOLERANCE,
    DOMAIN,
)
from .snapshot import (
    compile_name_pattern,
    find_snapshot,
    recording_window,
    snapshot_directories,
)

_LOGGER = logging.getLogger(__name__)

# Recordings read at the same time from one Reolink device. Cameras and NVRs
# accept few simultaneous playback connections.
MAX_PARALLEL_RECORDINGS = 3

# Seconds after which a hanging FFmpeg process is killed
FFMPEG_TIMEOUT = 30

# Width of the thumbnails in pixels
THUMBNAIL_WIDTH = 256

# Longest recording accepted in a thumbnail request
MAX_RECORDING_DURATION = dt.timedelta(hours=6)


class Recording(Protocol):
    """The attributes of a recording needed for its thumbnail."""

    @property
    def file_name(self) -> str:
        """Return the file name of the recording on the device."""

    @property
    def start_time(self) -> dt.datetime:
        """Return the start of the recording."""

    @property
    def end_time(self) -> dt.datetime:
        """Return the end of the recording."""


@dataclass(frozen=True)
class RecordingRef:
    """A recording identified by the thumbnail URL."""

    file_name: str
    start_time: dt.datetime
    end_time: dt.datetime


def get_normalized_thumb_path(base_dir: Path, file_name: str) -> Path:
    """Get a thumbnail path that is identical for both main and sub streams.

    Raises ValueError if the file name would leave the base directory.
    """
    video_path = PurePosixPath(file_name)
    if ".." in video_path.parts:
        raise ValueError(f"Invalid recording file name {file_name}")
    stem = video_path.stem

    # Match standard NVR/SD card convention:
    # Rec[MS]<channel>_<prefix><date>_<start_time>_<end_time>_<hex>
    # e.g., RecS02_DST20260711_004228_004257_3914880_BCECD
    # We extract: channel, prefix, date, and end_time (ignoring start_time and hex)
    match = re.match(r"^Rec[MS]([0-9A-Za-z]+)_([A-Za-z]*)(\d{8})_\d{6}_(\d{6})", stem)
    if match:
        channel, prefix, date, end_time = match.groups()
        normalized_stem = f"Rec{channel}_{prefix}{date}_{end_time}"
    else:
        # General fallback: remove "S" or "M" distinction if it starts with "Rec[MS]"
        normalized_stem = re.sub(r"^Rec[MS]", "Rec", stem)

    # Absolute device paths are stored below the base directory
    directories = [part for part in video_path.parent.parts if part != "/"]
    return base_dir.joinpath(*directories, f"{normalized_stem}.png")


def get_vod_type(host) -> VodRequestType:
    """VOD Type."""
    # For thumbnail generation, always prefer DOWNLOAD/PLAYBACK over streaming formats
    # FLV and RTMP streams don't work well with FFmpeg for single frame extraction
    if host.api.is_nvr:
        return VodRequestType.DOWNLOAD
    return VodRequestType.PLAYBACK


def generate_thumbnail(link: str, path: Path, image: bool = False) -> bool:
    """Generate thumb file from a recording or, if image is True, a still image.

    The file is written atomically, so an interrupted run leaves no broken
    thumbnail behind. Returns True if the thumbnail was created.
    This function does blocking I/O.
    """
    _LOGGER.debug("Starting FFmpeg for thumbnail - URL: %s, Output: %s", link, path)
    temp_path = path.with_name(f"{path.stem}.tmp{path.suffix}")

    if image:
        # Seeking a single image makes FFmpeg write no output at all
        input_options = {}
    else:
        # Probe less of the stream: one frame is needed, not exact stream info
        input_options = {"ss": 0, "analyzeduration": 1_000_000, "probesize": 1_000_000}

    try:
        process = (
            ffmpeg.input(link, **input_options)
            .filter("scale", THUMBNAIL_WIDTH, -1)
            .output(str(temp_path), vframes=1, loglevel="error")
            .overwrite_output()
            .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True)
        )
        try:
            _, stderr = process.communicate(timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            _LOGGER.warning(
                "FFmpeg did not finish within %s s for %s", FFMPEG_TIMEOUT, link
            )
            return False

        if process.returncode:
            _LOGGER.error(
                "FFmpeg error creating thumbnail for %s: %s",
                link,
                stderr.decode("utf8", "replace").strip() or "N/A",
            )
            return False
        if not temp_path.exists():
            _LOGGER.warning("FFmpeg did not create thumbnail %s from %s", path, link)
            return False

        os.replace(temp_path, path)
        _LOGGER.debug("Thumbnail successfully created: %s", path)
        return True

    except Exception as e:  # noqa: BLE001
        _LOGGER.error(
            "Unexpected error creating thumbnail for %s: %s", link, e, exc_info=True
        )
        return False
    finally:
        temp_path.unlink(missing_ok=True)


async def _async_find_external_snapshot(
    hass: HomeAssistant, host, channel: int, file: Recording
) -> Path | None:
    """Return an external snapshot taken during the recording, if configured."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return None
    options = entries[0].options
    template = options.get(CONF_SNAPSHOT_DIRECTORY)
    if not template:
        return None

    tz = dt_util.get_default_time_zone()
    tolerance = dt.timedelta(
        seconds=options.get(CONF_SNAPSHOT_TOLERANCE, DEFAULT_SNAPSHOT_TOLERANCE)
    )
    start, end = recording_window(file.start_time, file.end_time, tolerance, tz)
    try:
        pattern = compile_name_pattern(
            options.get(CONF_SNAPSHOT_PATTERN, DEFAULT_SNAPSHOT_PATTERN)
        )
        directories = snapshot_directories(
            template, host.api.camera_name(channel), channel, start, end, tz
        )
    except ValueError as err:
        _LOGGER.warning("Invalid external snapshot settings: %s", err)
        return None

    snapshot = await asyncio.to_thread(
        find_snapshot, directories, pattern, tz, start, end
    )
    if snapshot is None:
        _LOGGER.debug(
            "No external snapshot for %s in %s",
            file.file_name,
            ", ".join(str(directory) for directory in directories),
        )
    else:
        _LOGGER.debug("Using external snapshot %s for %s", snapshot, file.file_name)
    return snapshot


async def async_thumbnail_source(
    hass: HomeAssistant, host, channel: int, file: Recording
) -> tuple[str, bool]:
    """Return the FFmpeg input for a thumbnail and whether it is a still image.

    The input is an external snapshot if one matches, otherwise the recording.
    """
    snapshot = await _async_find_external_snapshot(hass, host, channel, file)
    if snapshot is not None:
        return str(snapshot), True

    # Always use the low resolution (sub) stream to save bandwidth and CPU
    _, reolink_url = await host.api.get_vod_source(
        channel, file.file_name, "sub", get_vod_type(host)
    )
    return reolink_url, False


class ThumbnailGenerator:
    """Create thumbnails on demand, at most once per recording at a time."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the generator."""
        self.hass = hass
        self.cache_dir = Path(hass.config.cache_path(DOMAIN))
        # Thumbnails were stored below www/ before, which is served without
        # authentication. They are moved to the cache directory when used.
        self.legacy_dir = Path(hass.config.path("www", "recordings"))
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._pending: dict[Path, asyncio.Task[bool]] = {}

        # Signs the thumbnail URLs, so the view only accepts recordings listed
        # by the media browser. URLs become invalid on restart, which is fine
        # as browsing creates new ones.
        self._url_key = secrets.token_bytes(32)

    def _signature(self, config_entry_id: str, channel: int | str, query: dict) -> str:
        """Return the signature of the parameters of a thumbnail URL."""
        message = json.dumps(
            [config_entry_id, str(channel), query["file"], query["start"], query["end"]]
        )
        return hmac.new(self._url_key, message.encode(), hashlib.sha256).hexdigest()

    def url(self, config_entry_id: str, channel: int, file: Recording) -> str:
        """Return the signed URL of the thumbnail view for a recording."""
        query = {
            "file": file.file_name,
            "start": file.start_time.isoformat(),
            "end": file.end_time.isoformat(),
        }
        query["sig"] = self._signature(config_entry_id, channel, query)
        return f"/api/{DOMAIN}/{config_entry_id}/{channel}?{urlencode(query)}"

    def parse_url(
        self, config_entry_id: str, channel: str, query: Mapping[str, str]
    ) -> RecordingRef:
        """Return the recording of a thumbnail URL created by url().

        Raises ValueError if the URL is incomplete, not signed by this
        instance or describes an implausible recording.
        """
        try:
            params = {key: query[key] for key in ("file", "start", "end", "sig")}
        except KeyError as err:
            raise ValueError(f"Missing parameter {err}") from err
        expected = self._signature(config_entry_id, channel, params)
        if not hmac.compare_digest(expected.encode(), params["sig"].encode()):
            raise ValueError("Invalid signature")

        recording = RecordingRef(
            file_name=params["file"],
            start_time=dt.datetime.fromisoformat(params["start"]),
            end_time=dt.datetime.fromisoformat(params["end"]),
        )
        duration = recording.end_time - recording.start_time
        if not dt.timedelta(0) <= duration <= MAX_RECORDING_DURATION:
            raise ValueError(f"Invalid recording duration {duration}")
        return recording

    def thumb_path(self, file_name: str) -> Path:
        """Return the cached thumbnail path of a recording."""
        return get_normalized_thumb_path(self.cache_dir, file_name)

    async def async_get(
        self, config_entry_id: str, channel: int, file: Recording
    ) -> Path | None:
        """Return the thumbnail of a recording, creating it if missing.

        Returns None if the thumbnail cannot be created.
        Raises ValueError if the file name is invalid.
        """
        path = self.thumb_path(file.file_name)
        legacy_path = get_normalized_thumb_path(self.legacy_dir, file.file_name)
        if await self.hass.async_add_executor_job(_restore_cached, path, legacy_path):
            return path

        if (task := self._pending.get(path)) is None:
            task = self.hass.async_create_background_task(
                self._async_generate(config_entry_id, channel, file, path),
                f"{DOMAIN} thumbnail {file.file_name}",
            )
            self._pending[path] = task
            task.add_done_callback(lambda _: self._pending.pop(path, None))

        # A cancelled request must not cancel a generation others wait for
        return path if await asyncio.shield(task) else None

    async def _async_generate(
        self, config_entry_id: str, channel: int, file: Recording, path: Path
    ) -> bool:
        """Create the thumbnail of a recording."""
        try:
            host = get_host(self.hass, config_entry_id)
            source, image = await async_thumbnail_source(self.hass, host, channel, file)
            await self.hass.async_add_executor_job(
                lambda: path.parent.mkdir(parents=True, exist_ok=True)
            )
            if image:
                return await asyncio.to_thread(generate_thumbnail, source, path, True)
            semaphore = self._semaphores.setdefault(
                config_entry_id, asyncio.Semaphore(MAX_PARALLEL_RECORDINGS)
            )
            async with semaphore:
                return await asyncio.to_thread(generate_thumbnail, source, path)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "Error preparing thumbnail generation for %s: %s",
                file.file_name,
                e,
                exc_info=True,
            )
            return False


def _restore_cached(path: Path, legacy_path: Path) -> bool:
    """Return True if the thumbnail is cached, moving a legacy one if needed."""
    if path.exists():
        return True
    if not legacy_path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(legacy_path, path)
    return True
