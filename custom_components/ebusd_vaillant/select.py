"""Select entities for ebusd Vaillant: the hot water preset (comfort / eco)."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredControl, _get

_LOGGER = logging.getLogger(__name__)

# ebusd value lists are 0=comfort;1=eco; depending on the ebusd version a message is
# published as the text or as the number.
_BY_NUMBER = {"0": "comfort", "1": "eco"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredControl) and e.kind == "select" and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdControlSelect(hass, e, coordinator))
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class EbusdControlSelect(SelectEntity):
    """A register with a fixed list of values."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredControl, coordinator: EbusdCoordinator
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_control_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_options = list(config.options)
        self._attr_current_option: str | None = None
        if config.entity_category:
            self._attr_entity_category = EntityCategory(config.entity_category)
        self._unsubscribe: Any = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            self._handle_value(_get(payload, self._config.topic.field))
            self.async_write_ha_state()

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self._config.topic.read_topic, _handle
        )
        self._handle_value(self._coordinator.get_current_value(self._config.topic))
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()

    @callback
    def _handle_value(self, value: Any) -> None:
        if value is None:
            return
        text = _BY_NUMBER.get(str(value), str(value)).lower()
        if text in self._attr_options:
            self._attr_current_option = text

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options or not self._config.topic.write_topic:
            return
        await mqtt.async_publish(self.hass, self._config.topic.write_topic, option)
        self._attr_current_option = option  # shown at once; ebusd republishes on success
        self.async_write_ha_state()
