"""Binary sensor for LLM Watch."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_URL, DOMAIN
from .coordinator import LlmWatchCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LlmWatchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LlmWatchFoundSensor(coordinator, entry)])


class LlmWatchFoundSensor(CoordinatorEntity[LlmWatchCoordinator], BinarySensorEntity):
    """On when the watched page contains what was asked for."""

    _attr_has_entity_name = True
    _attr_name = "Found"

    def __init__(self, coordinator: LlmWatchCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_found"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"LLM Watch: {coordinator.watch_name}",
            manufacturer="LLM Watch",
            configuration_url=coordinator.config[CONF_URL],
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
        return {
            "summary": self.coordinator.data.get("summary"),
            "items": self.coordinator.data.get("items"),
            "checked_at": self.coordinator.data.get("checked_at"),
            "url": self.coordinator.config[CONF_URL],
        }
