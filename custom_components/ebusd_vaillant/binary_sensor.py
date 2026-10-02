"""Binary sensors for ebusd Vaillant: low system pressure and eBUS connection."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LOW_PRESSURE,
    CONF_MQTT_PREFIX,
    DEFAULT_LOW_PRESSURE,
    DEFAULT_MQTT_PREFIX,
    DOMAIN,
)
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredPressureMonitor, DiscoveredZoneFlag, TopicConfig, _get

_TRUE = frozenset({"1", "on", "yes", "true"})
_FALSE = frozenset({"0", "off", "no", "false"})


def _flag(value: Any) -> bool | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _payload(raw: Any) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError, ValueError, TypeError:
        return raw


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    prefix = entry.data.get(CONF_MQTT_PREFIX, DEFAULT_MQTT_PREFIX)
    threshold = entry.options.get(CONF_LOW_PRESSURE, DEFAULT_LOW_PRESSURE)
    async_add_entities([EbusdConnectedBinarySensor(hass, prefix)])
    seen: set[str] = set()

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredPressureMonitor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdLowPressureBinarySensor(hass, e, threshold))
            elif isinstance(e, DiscoveredZoneFlag) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdZoneFlagBinarySensor(hass, e))
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class EbusdLowPressureBinarySensor(BinarySensorEntity):
    """On when the system pressure is below the threshold or the heat pump reports a loss."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "low_pressure"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredPressureMonitor, threshold: float
    ) -> None:
        self.hass = hass
        self._config = config
        self._threshold = threshold
        self._attr_unique_id = f"ebusd_low_pressure_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._pressure: float | None = None
        self._pressure_loss: bool | None = None
        self._unsubscribe: list[Any] = []

    async def async_added_to_hass(self) -> None:
        if self._config.pressure:
            await self._subscribe(self._config.pressure, self._handle_pressure)
        if self._config.pressure_loss:
            await self._subscribe(self._config.pressure_loss, self._handle_loss)

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()

    async def _subscribe(self, topic_cfg: TopicConfig, handler: Any) -> None:
        @callback
        def _wrap(msg: mqtt.ReceiveMessage) -> None:
            value = _get(_payload(msg.payload), topic_cfg.field)
            if value is not None:
                handler(value)
                self.async_write_ha_state()

        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, topic_cfg.read_topic, _wrap))

    @callback
    def _handle_pressure(self, value: Any) -> None:
        try:
            self._pressure = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_loss(self, value: Any) -> None:
        self._pressure_loss = _flag(value)

    @property
    def available(self) -> bool:
        return self._pressure is not None or self._pressure_loss is not None

    @property
    def is_on(self) -> bool | None:
        if self._pressure_loss:
            return True
        if self._pressure is None:
            return False if self._pressure_loss is False else None
        # 0 bar means "no reading" on some controllers, not an empty system
        return 0 < self._pressure < self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "pressure": self._pressure,
            "threshold": self._threshold,
            "pressure_loss": self._pressure_loss,
        }


class EbusdConnectedBinarySensor(BinarySensorEntity):
    """On while ebusd runs and has a signal on the eBUS (ebusd/global/running, signal)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "ebus_connected"
    _attr_entity_registry_enabled_default = False

    def __init__(self, hass: HomeAssistant, prefix: str) -> None:
        self.hass = hass
        self._prefix = prefix
        self._attr_unique_id = f"ebusd_connected_{prefix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, prefix)})
        self._signal: bool | None = None
        self._running: bool | None = None
        self._unsubscribe: list[Any] = []

    async def async_added_to_hass(self) -> None:
        for name in ("signal", "running"):
            self._unsubscribe.append(
                await mqtt.async_subscribe(
                    self.hass, f"{self._prefix}/global/{name}", self._handler(name)
                )
            )

    def _handler(self, name: str) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            setattr(self, f"_{name}", _flag(_payload(msg.payload)))
            self.async_write_ha_state()

        return _handle

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()

    @property
    def is_on(self) -> bool | None:
        if self._running is False or self._signal is False:
            return False
        if self._signal is None:
            return None
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"signal": self._signal, "running": self._running}


class EbusdZoneFlagBinarySensor(BinarySensorEntity):
    """An on/off value of a zone, such as whether a schedule time slot is active."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: DiscoveredZoneFlag) -> None:
        self.hass = hass
        self._config = config
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_zone_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_is_on: bool | None = None
        self._unsubscribe: Any = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            value = _flag(_get(_payload(msg.payload), self._config.topic.field))
            if value is not None:
                self._attr_is_on = value
                self.async_write_ha_state()

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self._config.topic.read_topic, _handle
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
