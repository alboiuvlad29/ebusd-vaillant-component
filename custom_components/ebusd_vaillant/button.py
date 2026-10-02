"""Button entities for ebusd Vaillant: start a one-time hot water charge."""

from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredWaterHeater

_LOGGER = logging.getLogger(__name__)


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
            if isinstance(e, DiscoveredWaterHeater) and e.sf_mode and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdHwcBoostButton(hass, e))
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class EbusdHwcBoostButton(ButtonEntity):
    """Start a one-time hot water charge (HwcSFMode=load); it ends by itself."""

    _attr_has_entity_name = True
    _attr_translation_key = "hot_water_boost_start"
    _attr_icon = "mdi:water-boiler-alert"

    def __init__(self, hass: HomeAssistant, config: DiscoveredWaterHeater) -> None:
        self.hass = hass
        self._config = config
        self._attr_unique_id = f"ebusd_boost_button_{config.key}"
        self._attr_device_info = build_device_info(config)

    async def async_press(self) -> None:
        cfg = self._config.sf_mode
        if cfg and cfg.write_topic:
            _LOGGER.debug("MQTT publish: %s -> load", cfg.write_topic)
            await mqtt.async_publish(self.hass, cfg.write_topic, "load")
