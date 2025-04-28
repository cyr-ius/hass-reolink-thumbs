"""The Reolink Thumbs component."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

import ffmpeg
from reolink_aio.enums import VodRequestType

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import BrowseMediaSource
from homeassistant.components.reolink.const import DOMAIN as REOLINK_DOMAIN
from homeassistant.components.reolink.media_source import (
    DUAL_LENS_MODELS,
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
    if filename.endswith(".mp4"):
        if host.api.is_nvr:
            return VodRequestType.DOWNLOAD
        return VodRequestType.PLAYBACK
    if host.api.is_nvr:
        return VodRequestType.FLV
    return VodRequestType.RTMP


def generate_thumbnail(link, path):
    """Generate thumb file."""
    _LOGGER.debug("% - %s ", link, path)
    ffmpeg.input(link, ss=0).filter("scale", 256, -1).output(str(path), vframes=1).run()


async def _async_generate_camera_files(
    self,
    config_entry_id: str,
    channel: int,
    stream: str,
    year: int,
    month: int,
    day: int,
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
    _, vod_files = await host.api.request_vod_files(channel, start, end, stream=stream)
    for file in vod_files:
        file_name = f"{file.start_time.time()} {file.duration}"
        if file.triggers != file.triggers.NONE:
            file_name += " " + " ".join(
                str(trigger.name).title()
                for trigger in file.triggers
                if trigger != trigger.NONE
            )

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

        children.append(
            BrowseMediaSource(
                domain=REOLINK_DOMAIN,
                identifier=f"FILE|{config_entry_id}|{channel}|{stream}|{file.file_name}",
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=file_name,
                can_play=True,
                can_expand=False,
                thumbnail=f"/local/recordings/{video_path.parent}/{video_path.stem}.png",
            )
        )

    title = f"{host.api.camera_name(channel)} {res_name(stream)} {year}/{month}/{day}"
    if host.api.model in DUAL_LENS_MODELS:
        title = f"{host.api.camera_name(channel)} lens {channel} {res_name(stream)} {year}/{month}/{day}"

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
