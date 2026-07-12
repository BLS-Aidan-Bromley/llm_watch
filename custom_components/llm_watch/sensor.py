"""Sensors for LLM Watch."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LlmWatchCoordinator
from .helpers import best_price


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
            [
                LlmWatchMatchesSensor(coordinator, subentry),
                LlmWatchBestPriceSensor(coordinator, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class _Base(CoordinatorEntity[LlmWatchCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: LlmWatchCoordinator, subentry: ConfigSubentry
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
        )

    @property
    def _items(self) -> list[dict]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("items") or []


class LlmWatchMatchesSensor(_Base):
    """How many matching items the last check found."""

    _attr_name = "Matches"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:magnify"

    def __init__(
        self, coordinator: LlmWatchCoordinator, subentry: ConfigSubentry
    ) -> None:
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_matches"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self._items)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        return {
            "items": self._items,
            "summary": self.coordinator.data.get("summary"),
            "checked_at": self.coordinator.data.get("checked_at"),
        }


class LlmWatchBestPriceSensor(_Base):
    """Lowest price among matching items, if any carry a price."""

    _attr_name = "Best price"
    _attr_icon = "mdi:currency-gbp"

    def __init__(
        self, coordinator: LlmWatchCoordinator, subentry: ConfigSubentry
    ) -> None:
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_best_price"

    @property
    def native_value(self) -> float | None:
        return best_price(self._items)

    @property
    def extra_state_attributes(self) -> dict | None:
        priced = [i for i in self._items if i.get("price") is not None]
        if not priced:
            return None
        cheapest = min(priced, key=lambda i: i["price"])
        return {
            "item": cheapest["name"],
            "link": cheapest.get("link") or cheapest.get("source"),
        }
