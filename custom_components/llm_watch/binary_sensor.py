"""Binary sensor for LLM Watch."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_URL, DOMAIN
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
            [LlmWatchFoundSensor(coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class LlmWatchFoundSensor(CoordinatorEntity[LlmWatchCoordinator], BinarySensorEntity):
    """On when the watch has found what was asked for."""

    _attr_has_entity_name = True
    _attr_name = "Found"

    def __init__(
        self, coordinator: LlmWatchCoordinator, subentry: ConfigSubentry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{subentry.subentry_id}_found"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=f"LLM Watch: {coordinator.watch_name}",
            manufacturer="LLM Watch",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get("found"))

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        attrs = {
            "summary": self.coordinator.data.get("summary"),
            "items": self.coordinator.data.get("items"),
            "checked_at": self.coordinator.data.get("checked_at"),
        }
        if self.coordinator.is_search:
            attrs["queries"] = self.coordinator.data.get("queries")
            attrs["pages_checked"] = self.coordinator.data.get("pages_checked")
        else:
            attrs["url"] = self.coordinator.config.get(CONF_URL)
        return attrs
