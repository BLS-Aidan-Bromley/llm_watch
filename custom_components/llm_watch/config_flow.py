"""Config flow for LLM Watch."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_CREATE_ANYWAY,
    CONF_MODE,
    CONF_MODEL,
    CONF_NAME,
    CONF_OLLAMA_URL,
    CONF_PROMPT,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_URL,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MODE_AUTO,
    MODES,
)
from .coordinator import run_check

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                TextSelectorConfig(type="url")
            ),
            vol.Required(
                CONF_PROMPT, default=defaults.get(CONF_PROMPT, "")
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Required(
                CONF_MODE, default=defaults.get(CONF_MODE, MODE_AUTO)
            ): SelectSelector(
                SelectSelectorConfig(options=MODES, translation_key="mode")
            ),
            vol.Required(
                CONF_OLLAMA_URL,
                default=defaults.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL),
            ): TextSelector(TextSelectorConfig(type="url")),
            vol.Required(
                CONF_MODEL, default=defaults.get(CONF_MODEL, DEFAULT_MODEL)
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL_HOURS,
                default=defaults.get(
                    CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=168, step=1, mode=NumberSelectorMode.BOX,
                    unit_of_measurement="hours",
                )
            ),
            vol.Optional(
                CONF_CREATE_ANYWAY, default=False
            ): bool,
        }
    )


class LlmWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a watch: collect config, run a live test, show a preview."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict[str, Any] | None = None
        self._preview: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL_HOURS] = int(
                user_input[CONF_SCAN_INTERVAL_HOURS]
            )
            create_anyway = user_input.pop(CONF_CREATE_ANYWAY, False)
            await self.async_set_unique_id(
                f"{user_input[CONF_URL]}::{user_input[CONF_PROMPT]}"[:255]
            )
            self._abort_if_unique_id_configured()

            if create_anyway:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

            session = async_get_clientsession(self.hass)
            try:
                self._preview = await run_check(session, user_input)
            except (TimeoutError, aiohttp.ClientError) as err:
                _LOGGER.warning("Test fetch failed: %s", err)
                errors["base"] = "cannot_connect"
            except UpdateFailed as err:
                _LOGGER.warning("Test run failed: %s", err)
                errors["base"] = "no_content"
            except ValueError as err:
                _LOGGER.warning("Model reply unusable: %s", err)
                errors["base"] = "bad_model_reply"
            else:
                self._pending = user_input
                return await self.async_step_preview()

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._pending is not None and self._preview is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending[CONF_NAME], data=self._pending
            )

        items = self._preview["items"]
        item_lines = "\n".join(
            f"- {i['name']}"
            + (f" — {i['price']}" if i.get("price") is not None else "")
            + (f" ({i['availability']})" if i.get("availability") else "")
            for i in items[:10]
        )
        return self.async_show_form(
            step_id="preview",
            data_schema=vol.Schema({}),
            description_placeholders={
                "found": "yes" if self._preview["found"] else "no",
                "summary": self._preview["summary"] or "(none)",
                "items": item_lines or "(none)",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LlmWatchOptionsFlow:
        return LlmWatchOptionsFlow()


class LlmWatchOptionsFlow(OptionsFlow):
    """Edit an existing watch without re-adding it."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL_HOURS] = int(
                user_input[CONF_SCAN_INTERVAL_HOURS]
            )
            user_input.pop(CONF_CREATE_ANYWAY, None)
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = _schema(current)
        # Name is fixed after creation; drop it and the test toggle.
        schema = vol.Schema(
            {
                key: val
                for key, val in schema.schema.items()
                if getattr(key, "schema", None) not in (CONF_NAME, CONF_CREATE_ANYWAY)
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
