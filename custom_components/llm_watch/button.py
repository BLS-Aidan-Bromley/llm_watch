"""Check-now button for LLM Watch."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import LlmWatchCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinators = hass.data[DOMAIN][entry.entry_id]
    for subentry in entry.subentries.values():
        coordinator = coordinators.get(subentry.subentry_id)
        if coordinator is None:
            continue
        async_add_entities(
            [LlmWatchCheckNowButton(coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class LlmWatchCheckNowButton(ButtonEntity):
    """Runs the watch immediately."""

    _attr_has_entity_name = True
    _attr_name = "Check now"
    _attr_icon = "mdi:magnify-scan"

    def __init__(
        self, coordinator: LlmWatchCoordinator, subentry: ConfigSubentry
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{subentry.subentry_id}_check_now"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
        )

    async def async_press(self) -> None:
        await self._coordinator.async_request_refresh()
