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
from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)

# Domain for this component
DOMAIN = "reolink_thumbs"

# Service name
SERVICE_GENERATE_THUMBNAILS = "generate_thumbnails"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set the config entry up."""

    ReolinkVODMediaSource._async_generate_camera_files = (  # type:ignore[private-member-access]
        _async_generate_camera_files
    )

    # Register service for manual thumbnail generation
    async def handle_generate_thumbnails(call: ServiceCall) -> None:
        """Handle the service call to generate thumbnails."""
        try:
            # Get the 'days' parameter from service call, default to 1 (today only)
            days = call.data.get("days", 1)
            entry = call.data.get("entry")
            _LOGGER.info(
                "Service called: reolink_thumbs.generate_thumbnails (days=%d)", days
            )
            await _generate_missing_thumbnails(hass, days, entry)
            _LOGGER.info("Thumbnail generation service completed successfully")
        except Exception as e:
            _LOGGER.error(
                "Error during thumbnail generation service: %s", e, exc_info=True
            )

    # Register the service (only once, check if not already registered)
    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_THUMBNAILS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_THUMBNAILS,
            handle_generate_thumbnails,
        )
        _LOGGER.info("Registered service: %s.%s", DOMAIN, SERVICE_GENERATE_THUMBNAILS)

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
            str(path), vframes=1, loglevel="error"
        ).run(capture_stdout=True, capture_stderr=True)

        _LOGGER.info("Thumbnail successfully created: %s", path)

    except ffmpeg.Error as e:
        _LOGGER.error(
            "FFmpeg error creating thumbnail for %s: stdout=%s, stderr=%s",
            link,
            e.stdout.decode("utf8") if e.stdout else "N/A",
            e.stderr.decode("utf8") if e.stderr else "N/A",
        )
        # Don't raise - allow media browser to continue without thumbnail
    except Exception as e:
        _LOGGER.error(
            "Unexpected error creating thumbnail for %s: %s",
            link,
            str(e),
            exc_info=True,
        )
        # Don't raise - allow media browser to continue without thumbnail


async def _generate_missing_thumbnails(
    hass: HomeAssistant, days: int = 1, entry_id: str | None = None
):
    """Generate missing thumbnails for recordings in the background.

    Args:
        hass: Home Assistant instance
        days: Number of days to look back (default 1 = today only)
        entry_id: Identifier
    """
    # Get Reolink config entries
    if entry_id:
        reolink_entries = [
            entry
            for entry in hass.config_entries.async_entries(REOLINK_DOMAIN)
            if entry.entry_id == entry_id
        ]
    else:
        reolink_entries = hass.config_entries.async_entries(REOLINK_DOMAIN)

    if not reolink_entries:
        _LOGGER.debug("No Reolink entries found for background thumbnail generation")
        return

    www_path = hass.config.path("www")
    today = dt.datetime.now()

    # Calculate date range
    start_date = today - dt.timedelta(days=days - 1)
    _LOGGER.info(
        "Generating thumbnails for last %d day(s) (from %s to %s)",
        days,
        start_date.strftime("%Y-%m-%d"),
        today.strftime("%Y-%m-%d"),
    )

    # Process each Reolink device
    for reolink_entry in reolink_entries:
        try:
            host = get_host(hass, reolink_entry.entry_id)

            # Get all channels for this device
            channels = list(host.api.channels)

            for channel in channels:
                # Use main stream for thumbnails
                stream = "main"

                # Process each day in the range
                for day_offset in range(days):
                    check_date = start_date + dt.timedelta(days=day_offset)

                    try:
                        # Request VOD files for this specific day
                        start = dt.datetime(
                            check_date.year,
                            check_date.month,
                            check_date.day,
                            hour=0,
                            minute=0,
                            second=0,
                        )
                        end = dt.datetime(
                            check_date.year,
                            check_date.month,
                            check_date.day,
                            hour=23,
                            minute=59,
                            second=59,
                        )

                        _, vod_files = await host.api.request_vod_files(
                            channel,
                            start,
                            end,
                            stream=stream,
                            split_time=VOD_SPLIT_TIME,
                        )

                        _LOGGER.debug(
                            "Found %s VOD files for camera %s channel %s on %s",
                            len(vod_files),
                            host.api.camera_name(channel),
                            channel,
                            check_date.strftime("%Y-%m-%d"),
                        )

                        # Process each file
                        for file in vod_files:
                            video_path = Path(file.file_name)
                            new_directory = Path(
                                f"{www_path}/recordings/{video_path.parent}"
                            )

                            # Ensure directory exists
                            if not Path.exists(new_directory):
                                new_directory.mkdir(parents=True, exist_ok=True)

                            thumb_path = Path(f"{new_directory}/{video_path.stem}.png")

                            # Only generate if missing
                            if not Path.exists(thumb_path):
                                try:
                                    vod_type = get_vod_type(host, file.file_name)
                                    _, reolink_url = await host.api.get_vod_source(
                                        channel, file.file_name, stream, vod_type
                                    )

                                    _LOGGER.debug(
                                        "Generating thumbnail for %s", file.file_name
                                    )
                                    await asyncio.to_thread(
                                        generate_thumbnail, reolink_url, thumb_path
                                    )

                                except Exception as e:
                                    _LOGGER.error(
                                        "Failed to generate thumbnail for %s: %s",
                                        file.file_name,
                                        e,
                                    )

                    except Exception as e:
                        # Check if it's a "no recordings" error (API code -17)
                        error_str = str(e)
                        if "'rspCode': -17" in error_str or "rcv failed" in error_str:
                            _LOGGER.debug(
                                "No recordings found for camera %s channel %s on %s (no SD card or no recordings)",
                                host.api.camera_name(channel),
                                channel,
                                check_date.strftime("%Y-%m-%d"),
                            )
                        else:
                            _LOGGER.error(
                                "Error processing channel %s for device %s: %s",
                                channel,
                                host.api.camera_name(channel),
                                e,
                            )

        except Exception as e:
            # Check if it's a "device not ready" error (runtime_data missing)
            error_str = str(e)
            if "runtime_data" in error_str or "has no attribute" in error_str:
                _LOGGER.debug(
                    "Reolink device %s (%s) not ready or offline, skipping thumbnail generation",
                    reolink_entry.title,
                    getattr(reolink_entry, "unique_id", "unknown"),
                )
            else:
                _LOGGER.error(
                    "Error processing Reolink device %s: %s", reolink_entry.title, e
                )


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
        trigger=event_trigger,
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

        # Try to generate thumbnail if it doesn't exist yet
        if not Path.exists(thumb_path):
            try:
                _LOGGER.info("Preparing thumbnail for %s", file.file_name)
                vod_type = get_vod_type(host, file.file_name)
                _, reolink_url = await host.api.get_vod_source(
                    channel, file.file_name, stream, vod_type
                )
                _LOGGER.info("Got VOD URL for %s: %s", file.file_name, reolink_url)

                # Generate thumbnail synchronously so we can see it immediately
                # This blocks the media browser slightly but ensures thumbnails are created
                await asyncio.to_thread(generate_thumbnail, reolink_url, thumb_path)

            except Exception as e:
                _LOGGER.error(
                    "Error preparing thumbnail generation for %s: %s",
                    file.file_name,
                    e,
                    exc_info=True,
                )
        else:
            _LOGGER.debug("Thumbnail already exists: %s", thumb_path)
        # ===== End custom =====

        # Only set thumbnail if file exists, otherwise None
        thumbnail_url = None
        if Path.exists(thumb_path):
            thumbnail_url = (
                f"/local/recordings/{video_path.parent}/{video_path.stem}.png"
            )

        children.append(
            BrowseMediaSource(
                domain=REOLINK_DOMAIN,
                identifier=f"FILE|{config_entry_id}|{channel}|{stream}|{file.file_name}|{file.start_time_id}|{file.end_time_id}",
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=file_name,
                can_play=True,
                can_expand=False,
                thumbnail=thumbnail_url,
            )
        )

    title = f"{host.api.camera_name(channel)} {res_name(stream)} {year}/{month}/{day}"
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
