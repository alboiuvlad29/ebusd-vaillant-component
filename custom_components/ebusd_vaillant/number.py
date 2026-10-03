"""Number entities for ebusd Vaillant heating circuits."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ALLOW_INSTALLER, DEFAULT_ALLOW_INSTALLER, DOMAIN
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredControl, DiscoveredCoolTempLimit, TopicConfig, _get

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    cool_temp_by_key: dict[str, EbusdCoolTempLimitEntity] = {}
    controls: set[str] = set()
    allow_installer = entry.options.get(CONF_ALLOW_INSTALLER, DEFAULT_ALLOW_INSTALLER)

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredCoolTempLimit):
                if e.key not in cool_temp_by_key:
                    entity = EbusdCoolTempLimitEntity(hass, e, coordinator)
                    cool_temp_by_key[e.key] = entity
                    new.append(entity)
            elif (
                isinstance(e, DiscoveredControl)
                and e.kind == "number"
                and (allow_installer or not e.installer)
                and e.key not in controls
            ):
                controls.add(e.key)
                new.append(EbusdControlNumber(hass, e, coordinator))
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class EbusdCoolTempLimitEntity(NumberEntity):
    """Minimum cooling temperature setpoint for a heating circuit (Hc{n}MinCoolTempDesired)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredCoolTempLimit, coordinator: EbusdCoordinator
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._unsubscribe: list[Any] = []
        self._attr_name = "Min Cooling Temperature"
        self._attr_unique_id = f"ebusd_cool_temp_limit_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_min_value = config.min_temp
        self._attr_native_max_value = config.max_temp
        self._attr_native_step = config.temp_step
        self._attr_native_value: float | None = None

    async def _subscribe(self, topic_cfg: TopicConfig, handler: Any) -> None:
        @callback
        def _wrap(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, topic_cfg.field)
            if value is not None:
                handler(value)
                self.async_write_ha_state()

        unsub = await mqtt.async_subscribe(self.hass, topic_cfg.read_topic, _wrap)
        self._unsubscribe.append(unsub)

    def _seed(self, topic_cfg: TopicConfig, handler: Any) -> None:
        if (v := self._coordinator.get_current_value(topic_cfg)) is not None:
            handler(v)

    async def async_added_to_hass(self) -> None:
        await self._subscribe(self._config.cool_temp, self._handle_value)
        self._seed(self._config.cool_temp, self._handle_value)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()

    @callback
    def _handle_value(self, value: Any) -> None:
        try:
            self._attr_native_value = float(value)
        except TypeError, ValueError:
            pass

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.async_write_setpoint(self._config.cool_temp, str(value))


class EbusdControlNumber(NumberEntity):
    """A numeric setting: the hot water eco temperature and the installer values."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredControl, coordinator: EbusdCoordinator
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_control_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_min_value = config.min_value
        self._attr_native_max_value = config.max_value
        self._attr_native_step = config.step
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_class = config.device_class  # type: ignore[assignment]
        if config.entity_category:
            self._attr_entity_category = EntityCategory(config.entity_category)
        self._attr_native_value: float | None = None
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
        try:
            self._attr_native_value = float(value)
        except TypeError, ValueError:
            pass

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.async_write_setpoint(self._config.topic, str(value))
