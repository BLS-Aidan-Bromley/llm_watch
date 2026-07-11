"""LLM Watch: describe what you want, give it a URL, get a sensor."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import ATTR_WATCH_NAME, DOMAIN, PLATFORMS, SERVICE_RUN_WATCH
from .coordinator import LlmWatchCoordinator

_LOGGER = logging.getLogger(__name__)

RUN_WATCH_SCHEMA = vol.Schema({vol.Optional(ATTR_WATCH_NAME): str})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a watch from a config entry."""
    coordinator = LlmWatchCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_WATCH):

        async def _run_watch(call: ServiceCall) -> None:
            """Refresh one watch by name, or all watches."""
            wanted = call.data.get(ATTR_WATCH_NAME)
            coordinators: dict[str, LlmWatchCoordinator] = hass.data.get(DOMAIN, {})
            matched = False
            for coord in coordinators.values():
                if wanted is None or coord.watch_name == wanted:
                    matched = True
                    await coord.async_request_refresh()
            if wanted is not None and not matched:
                _LOGGER.warning("llm_watch.run_watch: no watch named '%s'", wanted)

        hass.services.async_register(
            DOMAIN, SERVICE_RUN_WATCH, _run_watch, schema=RUN_WATCH_SCHEMA
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a watch."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RUN_WATCH)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
