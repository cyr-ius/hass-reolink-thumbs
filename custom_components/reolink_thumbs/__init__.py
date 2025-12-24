"""The Reolink Thumbs component."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

import ffmpeg
from reolink_aio.enums import VodRequestType
from reolink_aio.typings import VOD_trigger
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import BrowseMediaSource
from homeassistant.components.reolink.const import DOMAIN as REOLINK_DOMAIN
from homeassistant.components.reolink.media_source import (
    DUAL_LENS_MODELS,
    VOD_SPLIT_TIME,
    ReolinkVODMediaSource,
    res_name,
)
from homeassistant.components.reolink.util import get_host
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set the config entry up."""

    ReolinkVODMediaSource._async_generate_camera_files = (  # type:ignore[private-member-access]
        _async_generate_camera_files
    )

    return True


def get_vod_type(host, filename) -> VodRequestType:
    """VOD Type."""
    # For thumbnail generation, always prefer DOWNLOAD/PLAYBACK over streaming formats
    # FLV and RTMP streams don't work well with FFmpeg for single frame extraction
    if host.api.is_nvr:
        return VodRequestType.DOWNLOAD
    return VodRequestType.PLAYBACK


def generate_thumbnail(link, path):
    """Generate thumb file."""
    try:
        _LOGGER.info("Starting FFmpeg for thumbnail - URL: %s, Output: %s", link, path)
        
        # Run FFmpeg and capture output for better error messages
        ffmpeg.input(link, ss=0).filter("scale", 256, -1).output(
            str(path), vframes=1, loglevel='error'
        ).run(capture_stdout=True, capture_stderr=True)
        
        _LOGGER.info("Thumbnail successfully created: %s", path)
        
    except ffmpeg.Error as e:
        _LOGGER.error(
            "FFmpeg error creating thumbnail for %s: stdout=%s, stderr=%s",
            link,
            e.stdout.decode('utf8') if e.stdout else 'N/A',
            e.stderr.decode('utf8') if e.stderr else 'N/A'
        )
        # Don't raise - allow media browser to continue without thumbnail
    except Exception as e:
        _LOGGER.error("Unexpected error creating thumbnail for %s: %s", link, str(e), exc_info=True)
        # Don't raise - allow media browser to continue without thumbnail


async def _async_generate_camera_files(
    self,
    config_entry_id: str,
    channel: int,
    stream: str,
    year: int,
    month: int,
    day: int,
    event: str | None = None,
) -> BrowseMediaSource:
    """Return all recording files on a specific day of a Reolink camera."""
    host = get_host(self.hass, config_entry_id)
    www_path = self.hass.config.path("www")

    start = dt.datetime(year, month, day, hour=0, minute=0, second=0)
    end = dt.datetime(year, month, day, hour=23, minute=59, second=59)

    children: list[BrowseMediaSource] = []
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "Requesting VODs of %s on %s/%s/%s",
            host.api.camera_name(channel),
            year,
            month,
            day,
        )
    event_trigger = VOD_trigger[event] if event is not None else None
    _, vod_files = await host.api.request_vod_files(
        channel,
        start,
        end,
        stream=stream,
        split_time=VOD_SPLIT_TIME,
        trigger=event_trigger
    )

    if event is None and host.api.is_nvr and not host.api.is_hub:
        triggers = VOD_trigger.NONE
        for file in vod_files:
            triggers |= file.triggers

        children.extend(
            BrowseMediaSource(
                domain=REOLINK_DOMAIN,
                identifier=f"EVE|{config_entry_id}|{channel}|{stream}|{year}|{month}|{day}|{trigger.name}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                title=str(trigger.name).title(),
                can_play=False,
                can_expand=True,
            )
            for trigger in triggers
        )

    for file in vod_files:
        file_name = f"{file.start_time.time()} {file.duration}"
        if file.triggers != file.triggers.NONE:
            file_name += " " + " ".join(
                str(trigger.name).title()
                for trigger in file.triggers
                if trigger != trigger.NONE
            )

        # Add custom to display thumbs
        video_path = Path(file.file_name)
        new_directory = Path(f"{www_path}/recordings/{video_path.parent}")
        if not Path.exists(new_directory):
            new_directory.mkdir(parents=True, exist_ok=True)

        thumb_path = Path(f"{new_directory}/{video_path.stem}.png")
        if not Path.exists(thumb_path):
            vod_type = get_vod_type(host, file.file_name)
            _, reolink_url = await host.api.get_vod_source(
                channel, file.file_name, stream, vod_type
            )
            await asyncio.create_task(
                asyncio.to_thread(generate_thumbnail, reolink_url, thumb_path)
            )
        # ===== End custom =====

        children.append(
            BrowseMediaSource(
                domain=REOLINK_DOMAIN,
                identifier=f"FILE|{config_entry_id}|{channel}|{stream}|{file.file_name}|{file.start_time_id}|{file.end_time_id}",
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=file_name,
                can_play=True,
                can_expand=False,
                thumbnail=f"/local/recordings/{video_path.parent}/{video_path.stem}.png",
            )
        )

    title = (
        f"{host.api.camera_name(channel)} {res_name(stream)} {year}/{month}/{day}"
    )
    if host.api.model in DUAL_LENS_MODELS:
        title = f"{host.api.camera_name(channel)} lens {channel} {res_name(stream)} {year}/{month}/{day}"
    if event:
        title = f"{title} {event.title()}"

    return BrowseMediaSource(
        domain=REOLINK_DOMAIN,
        identifier=f"FILES|{config_entry_id}|{channel}|{stream}",
        media_class=MediaClass.CHANNEL,
        media_content_type=MediaType.PLAYLIST,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
    )
