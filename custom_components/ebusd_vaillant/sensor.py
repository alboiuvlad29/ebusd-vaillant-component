"""Sensor entities for ebusd Vaillant."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredErrorSensor, DiscoveredSensor, _get


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
            if isinstance(e, DiscoveredSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdSensor(hass, e))
            elif isinstance(e, DiscoveredErrorSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdErrorSensor(hass, e))
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class _EbusdNumericSensor(SensorEntity):
    """Base class: subscribes to one MQTT topic and exposes a float value."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, read_topic: str, field: str) -> None:
        self.hass = hass
        self._read_topic = read_topic
        self._field = field
        self._attr_native_value: float | None = None
        self._unsubscribe: Any = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, self._field)
            if value is not None:
                try:
                    self._attr_native_value = float(value)
                    self.async_write_ha_state()
                except TypeError, ValueError:
                    pass

        self._unsubscribe = await mqtt.async_subscribe(self.hass, self._read_topic, _handle)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()


class EbusdSensor(_EbusdNumericSensor):
    """Generic numeric sensor for ebusd Vaillant (energy, power, COP, pressure)."""

    def __init__(self, hass: HomeAssistant, config: DiscoveredSensor) -> None:
        super().__init__(hass, config.topic.read_topic, config.topic.field)
        self._attr_name = config.name
        self._attr_unique_id = f"{config.unique_id_prefix}_{config.key}"
        self._attr_device_class = (
            SensorDeviceClass(config.device_class) if config.device_class else None
        )
        self._attr_state_class = SensorStateClass(config.state_class)
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_info = build_device_info(config)


def error_codes(payload: Any) -> list[str]:
    """Codes from a Currenterror payload ({"error": {"value": X}, "error_1": ...}); null = none."""
    if not isinstance(payload, dict):
        return []
    codes = []
    for name in sorted(payload):
        value = payload[name]
        if isinstance(value, dict):
            value = value.get("value")
        if value not in (None, "", "-", 0, "0", 65535, "65535"):
            codes.append(str(value))
    return codes


class EbusdErrorSensor(SensorEntity):
    """Current error codes of one device; raises a Repairs issue while a code is present."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "current_error"
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, hass: HomeAssistant, config: DiscoveredErrorSensor) -> None:
        self.hass = hass
        self._config = config
        self._attr_unique_id = f"ebusd_current_error_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_value: str | None = None
        self._codes: list[str] = []
        self._unsubscribe: Any = None

    @property
    def _issue_id(self) -> str:
        return f"device_error_{self._config.key}"

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                return
            self._update(error_codes(payload))
            self.async_write_ha_state()

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self._config.topic.read_topic, _handle
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()

    @callback
    def _update(self, codes: list[str]) -> None:
        self._codes = codes
        self._attr_native_value = ", ".join(codes) if codes else "none"
        if codes:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_error",
                translation_placeholders={
                    "device": self._config.device_name,
                    "codes": self._attr_native_value,
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"codes": self._codes}
