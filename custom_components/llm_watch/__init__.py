"""LLM Watch: describe what you want, it watches the web for it."""

from __future__ import annotations

import logging
from types import MappingProxyType

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    ATTR_WATCH_NAME,
    CONF_AI_TASK_ENTITY,
    CONF_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_RUN_WATCH,
    SUBENTRY_PAGE_WATCH,
)
from .coordinator import LlmWatchCoordinator

_LOGGER = logging.getLogger(__name__)

RUN_WATCH_SCHEMA = vol.Schema({vol.Optional(ATTR_WATCH_NAME): str})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub and one coordinator per watch subentry."""
    coordinators: dict[str, LlmWatchCoordinator] = {}
    for subentry in entry.subentries.values():
        coordinator = LlmWatchCoordinator(hass, entry, subentry)
        # A failing watch must not block the whole hub; entities show
        # unavailable and the coordinator retries on its schedule.
        await coordinator.async_refresh()
        coordinators[subentry.subentry_id] = coordinator

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_WATCH):

        async def _run_watch(call: ServiceCall) -> None:
            """Refresh one watch by name, or all watches."""
            wanted = call.data.get(ATTR_WATCH_NAME)
            matched = False
            for entry_coordinators in hass.data.get(DOMAIN, {}).values():
                for coord in entry_coordinators.values():
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
    """Unload the hub."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RUN_WATCH)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the hub or its subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a v1 single-watch entry into a v2 hub with one subentry."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        old = {**entry.data, **entry.options}
        subentry_data = {
            k: v
            for k, v in old.items()
            if k not in (CONF_AI_TASK_ENTITY, "ollama_url", "model")
        }
        hub_data = {}
        if old.get(CONF_AI_TASK_ENTITY):
            hub_data[CONF_AI_TASK_ENTITY] = old[CONF_AI_TASK_ENTITY]
        hass.config_entries.async_update_entry(
            entry,
            title="LLM Watch",
            data=hub_data,
            options={},
            unique_id=DOMAIN,
            version=2,
        )
        if CONF_URL in subentry_data:
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(subentry_data),
                    subentry_type=SUBENTRY_PAGE_WATCH,
                    title=subentry_data.get("name", "Watch"),
                    unique_id=None,
                ),
            )
        _LOGGER.info("Migrated LLM Watch entry to v2 hub layout")
    return True
