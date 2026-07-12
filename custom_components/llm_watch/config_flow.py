"""Config flow for LLM Watch: hub entry plus watch subentries."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
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
    BACKENDS,
    CONF_AI_TASK_ENTITY,
    CONF_BACKEND,
    CONF_BRAVE_API_KEY,
    CONF_TAVILY_API_KEY,
    CONF_CREATE_ANYWAY,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_NAME,
    CONF_PROMPT,
    CONF_BLOCKLIST,
    CONF_REQUIRE_IN_STOCK,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_SHOPPING_ONLY,
    CONF_SEARXNG_URL,
    CONF_SITES,
    CONF_URL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MODE_AUTO,
    MODES,
    SUBENTRY_PAGE_WATCH,
    SUBENTRY_SEARCH_WATCH,
)
from .coordinator import run_page_check, run_search_check
from .search import backend_ready

_LOGGER = logging.getLogger(__name__)


def _optional(schema: dict, key: str, defaults: dict, sel: Any) -> None:
    """Add an optional field, only defaulting when a value is stored."""
    if defaults.get(key) not in (None, ""):
        schema[vol.Optional(key, default=defaults[key])] = sel
    else:
        schema[vol.Optional(key)] = sel


def _hub_schema(defaults: dict[str, Any]) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_BACKEND, default=defaults.get(CONF_BACKEND, BACKENDS[0])
        ): SelectSelector(
            SelectSelectorConfig(options=BACKENDS, translation_key="backend")
        ),
    }
    _optional(
        schema,
        CONF_TAVILY_API_KEY,
        defaults,
        TextSelector(TextSelectorConfig(type="password")),
    )
    _optional(
        schema, CONF_SEARXNG_URL, defaults, TextSelector(TextSelectorConfig(type="url"))
    )
    _optional(
        schema,
        CONF_BRAVE_API_KEY,
        defaults,
        TextSelector(TextSelectorConfig(type="password")),
    )
    _optional(
        schema,
        CONF_AI_TASK_ENTITY,
        defaults,
        EntitySelector(EntitySelectorConfig(domain="ai_task")),
    )
    return vol.Schema(schema)


_BACKEND_FIELD = {
    "searxng": CONF_SEARXNG_URL,
    "tavily": CONF_TAVILY_API_KEY,
    "brave": CONF_BRAVE_API_KEY,
}


def _hub_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """The chosen backend's field must be filled in."""
    field = _BACKEND_FIELD[user_input[CONF_BACKEND]]
    if not user_input.get(field):
        return {"base": f"missing_{field}"}
    return {}


def _watch_schema(kind: str, defaults: dict[str, Any]) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
    }
    if kind == SUBENTRY_PAGE_WATCH:
        schema[vol.Required(CONF_URL, default=defaults.get(CONF_URL, ""))] = (
            TextSelector(TextSelectorConfig(type="url"))
        )
    schema[vol.Required(CONF_PROMPT, default=defaults.get(CONF_PROMPT, ""))] = (
        TextSelector(TextSelectorConfig(multiline=True))
    )
    if kind == SUBENTRY_PAGE_WATCH:
        schema[
            vol.Required(CONF_MODE, default=defaults.get(CONF_MODE, MODE_AUTO))
        ] = SelectSelector(SelectSelectorConfig(options=MODES, translation_key="mode"))
    else:
        _optional(schema, CONF_SITES, defaults, TextSelector(TextSelectorConfig()))
    if kind == SUBENTRY_SEARCH_WATCH:
        schema[
            vol.Required(
                CONF_SHOPPING_ONLY,
                default=defaults.get(CONF_SHOPPING_ONLY, True),
            )
        ] = bool
        _optional(
            schema,
            CONF_BLOCKLIST,
            defaults,
            TextSelector(TextSelectorConfig(multiline=True)),
        )
    schema[
        vol.Required(
            CONF_REQUIRE_IN_STOCK,
            default=defaults.get(CONF_REQUIRE_IN_STOCK, False),
        )
    ] = bool
    _optional(
        schema,
        CONF_MAX_PRICE,
        defaults,
        NumberSelector(
            NumberSelectorConfig(min=0, step=0.01, mode=NumberSelectorMode.BOX)
        ),
    )
    _optional(
        schema,
        CONF_AI_TASK_ENTITY,
        defaults,
        EntitySelector(EntitySelectorConfig(domain="ai_task")),
    )
    schema[
        vol.Required(
            CONF_SCAN_INTERVAL_HOURS,
            default=defaults.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
        )
    ] = NumberSelector(
        NumberSelectorConfig(
            min=1, max=168, step=1, mode=NumberSelectorMode.BOX,
            unit_of_measurement="hours",
        )
    )
    schema[vol.Optional(CONF_CREATE_ANYWAY, default=False)] = bool
    return vol.Schema(schema)


def _normalise_watch_input(user_input: dict[str, Any]) -> bool:
    """Coerce numbers and pop the test toggle. Returns create_anyway."""
    user_input[CONF_SCAN_INTERVAL_HOURS] = int(user_input[CONF_SCAN_INTERVAL_HOURS])
    if user_input.get(CONF_MAX_PRICE) is not None:
        user_input[CONF_MAX_PRICE] = float(user_input[CONF_MAX_PRICE])
    return bool(user_input.pop(CONF_CREATE_ANYWAY, False))


async def _test_watch(
    hass: HomeAssistant, kind: str, config: dict[str, Any], hub: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Run a watch once; return (preview, error_key)."""
    try:
        if kind == SUBENTRY_SEARCH_WATCH:
            return await run_search_check(hass, config, hub), None
        return await run_page_check(hass, config, hub), None
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning("Test fetch failed: %s", err)
        return None, "cannot_connect"
    except UpdateFailed as err:
        _LOGGER.warning("Test run failed: %s", err)
        return None, "no_content"
    except HomeAssistantError as err:
        _LOGGER.warning("AI task failed: %s", err)
        return None, "ai_task_error"
    except ValueError as err:
        _LOGGER.warning("Model reply unusable: %s", err)
        return None, "bad_model_reply"


def _preview_placeholders(preview: dict[str, Any]) -> dict[str, str]:
    items = preview["items"]
    lines = []
    for i in items[:10]:
        head = f"- {i['name']}"
        if i.get("price") is not None:
            head += f" — {i['price']}"
        if i.get("availability"):
            head += f" ({i['availability']})"
        lines.append(head)
        # URL on its own line, nothing adjacent, so clients don't fold a
        # trailing bracket into the link.
        link = i.get("link") or i.get("source")
        if link:
            lines.append(f"  {link}")
    return {
        "found": "yes" if preview["found"] else "no",
        "summary": preview["summary"] or "(none)",
        "items": "\n".join(lines) or "(none)",
    }


class LlmWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the hub. Watches are added as subentries."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _hub_errors(user_input)
            if not errors:
                return self.async_create_entry(title="LLM Watch", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_hub_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _hub_errors(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_hub_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_PAGE_WATCH: PageWatchSubentryFlow,
            SUBENTRY_SEARCH_WATCH: SearchWatchSubentryFlow,
        }


class _WatchSubentryFlow(ConfigSubentryFlow):
    """Shared add/reconfigure flow for both watch types."""

    kind: str

    def __init__(self) -> None:
        self._pending: dict[str, Any] | None = None
        self._preview: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        hub = dict(self._get_entry().data)
        if user_input is not None:
            create_anyway = _normalise_watch_input(user_input)
            if self.kind == SUBENTRY_SEARCH_WATCH and backend_ready(hub) is None:
                errors["base"] = "no_backend"
            elif create_anyway:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )
            else:
                self._preview, error = await _test_watch(
                    self.hass, self.kind, user_input, hub
                )
                if error:
                    errors["base"] = error
                else:
                    self._pending = user_input
                    return await self.async_step_preview()
        return self.async_show_form(
            step_id="user",
            data_schema=_watch_schema(self.kind, user_input or {}),
            errors=errors,
        )

    async def async_step_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        assert self._pending is not None and self._preview is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending[CONF_NAME], data=self._pending
            )
        return self.async_show_form(
            step_id="preview",
            data_schema=vol.Schema({}),
            description_placeholders=_preview_placeholders(self._preview),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            _normalise_watch_input(user_input)
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data=user_input,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_watch_schema(self.kind, dict(subentry.data)),
        )


class PageWatchSubentryFlow(_WatchSubentryFlow):
    kind = SUBENTRY_PAGE_WATCH


class SearchWatchSubentryFlow(_WatchSubentryFlow):
    kind = SUBENTRY_SEARCH_WATCH
