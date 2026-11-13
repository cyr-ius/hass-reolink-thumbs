"""Config flow to configure."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_SNAPSHOT_DIRECTORY,
    CONF_SNAPSHOT_PATTERN,
    CONF_SNAPSHOT_TOLERANCE,
    DEFAULT_SNAPSHOT_PATTERN,
    DEFAULT_SNAPSHOT_TOLERANCE,
    DOMAIN,
)
from .snapshot import (
    DIRECTORY_FIELDS,
    compile_name_pattern,
    validate_directory_template,
)


class ReolinkThumbsFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        return self.async_create_entry(title=DOMAIN, data={})

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ReolinkThumbsOptionsFlow:
        """Get the options flow for this handler."""
        return ReolinkThumbsOptionsFlow()


class ReolinkThumbsOptionsFlow(config_entries.OptionsFlow):
    """Handle the options (external snapshot source)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            directory = user_input.get(CONF_SNAPSHOT_DIRECTORY, "").strip()
            pattern = user_input[CONF_SNAPSHOT_PATTERN].strip()
            if directory:
                try:
                    validate_directory_template(directory)
                except ValueError:
                    errors[CONF_SNAPSHOT_DIRECTORY] = "invalid_directory"
            try:
                compile_name_pattern(pattern)
            except ValueError:
                errors[CONF_SNAPSHOT_PATTERN] = "invalid_pattern"
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_SNAPSHOT_DIRECTORY: directory,
                        CONF_SNAPSHOT_PATTERN: pattern,
                        CONF_SNAPSHOT_TOLERANCE: int(
                            user_input[CONF_SNAPSHOT_TOLERANCE]
                        ),
                    }
                )

        options = user_input or self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SNAPSHOT_DIRECTORY,
                    description={
                        "suggested_value": options.get(CONF_SNAPSHOT_DIRECTORY, "")
                    },
                ): selector.TextSelector(),
                vol.Required(
                    CONF_SNAPSHOT_PATTERN,
                    default=options.get(
                        CONF_SNAPSHOT_PATTERN, DEFAULT_SNAPSHOT_PATTERN
                    ),
                ): selector.TextSelector(),
                vol.Required(
                    CONF_SNAPSHOT_TOLERANCE,
                    default=options.get(
                        CONF_SNAPSHOT_TOLERANCE, DEFAULT_SNAPSHOT_TOLERANCE
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=300,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "fields": ", ".join(f"{{{field}}}" for field in DIRECTORY_FIELDS),
                "directory_example": "/media/snapshots/{camera_slug}/{date:%Y-%m-%d}",
                "pattern_example": "Snapshot_%Y%m%d_%H%M%S_person*",
            },
        )
