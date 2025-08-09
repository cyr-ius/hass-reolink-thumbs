"""Config flow to configure."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class HeatzyFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        return self.async_create_entry(title=DOMAIN, data={})
