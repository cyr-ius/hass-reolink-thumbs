"""HTTP view serving thumbnails of Reolink recordings."""

from __future__ import annotations

from http import HTTPStatus

from aiohttp import hdrs, web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.reolink.const import DOMAIN as REOLINK_DOMAIN

from .const import DOMAIN
from .thumbnail import ThumbnailGenerator

# Thumbnails of a recording never change
CACHE_CONTROL = "private, max-age=604800, immutable"


class ThumbnailView(HomeAssistantView):
    """Serve the thumbnail of a recording, creating it on first request."""

    url = f"/api/{DOMAIN}/{{config_entry_id}}/{{channel}}"
    name = f"api:{DOMAIN}:thumbnail"
    requires_auth = True

    def __init__(self, generator: ThumbnailGenerator) -> None:
        """Initialize the view."""
        self.generator = generator

    async def get(
        self, request: web.Request, config_entry_id: str, channel: str
    ) -> web.StreamResponse:
        """Return the thumbnail of the recording given in the query."""
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(config_entry_id)
        if entry is None or entry.domain != REOLINK_DOMAIN:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        try:
            recording = self.generator.parse_url(
                config_entry_id, channel, request.query
            )
            path = await self.generator.async_get(
                config_entry_id, int(channel), recording
            )
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)

        if path is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(path, headers={hdrs.CACHE_CONTROL: CACHE_CONTROL})
