"""Sensor entities for ebusd Vaillant."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .activity import (
    ACTIVITIES,
    ACTIVITY_COOLING,
    ACTIVITY_DEFROST,
    ACTIVITY_HEATING,
    ACTIVITY_HOT_WATER,
    ACTIVITY_IDLE,
    compute_activity,
)
from .const import DOMAIN
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import DiscoveredErrorSensor, DiscoveredOperatingMode, DiscoveredSensor, _get


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()
    followers: dict[str, list[_ActivityFollower]] = {}

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdSensor(hass, e))
            elif isinstance(e, DiscoveredErrorSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdErrorSensor(hass, e))
            elif isinstance(e, DiscoveredOperatingMode):
                if e.key in followers:
                    for entity in followers[e.key]:
                        hass.async_create_task(entity.async_update_config(e, coordinator))
                    continue
                group: list[_ActivityFollower] = [EbusdOperatingModeSensor(hass, e)]
                group.extend(EbusdEnergySplitSensor(hass, e, bucket) for bucket in ENERGY_BUCKETS)
                followers[e.key] = group
                new.extend(group)
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


# Energy buckets and the activities booked into each.
ENERGY_BUCKETS: dict[str, frozenset[str]] = {
    "heating": frozenset({ACTIVITY_HEATING, ACTIVITY_COOLING, ACTIVITY_DEFROST}),
    "hot_water": frozenset({ACTIVITY_HOT_WATER}),
    "standby": frozenset({ACTIVITY_IDLE}),
}
# Longer gaps between samples are not integrated (missing data, restarts).
_MAX_GAP_SECONDS = 1800


class _ActivityFollower:
    """Mixin: subscribe to the activity signals and keep the current activity."""

    hass: HomeAssistant
    _config: DiscoveredOperatingMode

    def _init_activity(self) -> None:
        self._activity_values: dict[str, Any] = {}
        self._activity: str | None = None
        self._unsubscribe: list[Any] = []
        self._subscribed: set[str] = set()

    async def async_update_config(
        self, config: DiscoveredOperatingMode, coordinator: EbusdCoordinator
    ) -> None:
        """Subscribe to activity signals (and power) discovered after creation."""
        self._config = config
        if self.platform is None:
            return
        new = [
            (name, cfg)
            for name, cfg in config.activity.items()
            if cfg is not None and name not in self._subscribed
        ]
        await self._subscribe_activity()
        await self._subscribe_extra()
        for name, cfg in new:  # seed with the value that announced the new signal
            value = coordinator.get_current_value(cfg)
            if value is not None:
                self._activity_values[name] = value
        if new:
            self._on_activity(compute_activity(self._activity_values))
        self.async_write_ha_state()

    async def _subscribe_extra(self) -> None:
        """Hook for subclasses with more topics."""

    async def _subscribe_activity(self) -> None:
        for name, cfg in self._config.activity.items():
            if cfg is not None and name not in self._subscribed:
                self._subscribed.add(name)
                self._unsubscribe.append(
                    await mqtt.async_subscribe(
                        self.hass, cfg.read_topic, self._activity_handler(name, cfg.field)
                    )
                )

    def _activity_handler(self, name: str, field: str) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, field)
            if value is None:
                return
            self._activity_values[name] = value
            self._on_activity(compute_activity(self._activity_values))

        return _handle

    def _on_activity(self, activity: str | None) -> None:
        raise NotImplementedError

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()


class EbusdOperatingModeSensor(_ActivityFollower, SensorEntity):
    """What the heat pump is doing: heating, cooling, hot water, defrost or idle."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "operating_mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ACTIVITIES
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, hass: HomeAssistant, config: DiscoveredOperatingMode) -> None:
        self.hass = hass
        self._config = config
        self._attr_unique_id = f"ebusd_operating_mode_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_value: str | None = None
        self._init_activity()

    async def async_added_to_hass(self) -> None:
        await self._subscribe_activity()

    @callback
    def _on_activity(self, activity: str | None) -> None:
        if activity != self._attr_native_value:
            self._attr_native_value = activity
            self.async_write_ha_state()


class EbusdEnergySplitSensor(_ActivityFollower, RestoreSensor):
    """Electrical energy used while heating, making hot water or standing by (kWh).

    Integrates the heat pump's electrical power input (left Riemann sum) and books
    each interval to the bucket of the operating mode during that interval.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_entity_registry_enabled_default = False

    def __init__(self, hass: HomeAssistant, config: DiscoveredOperatingMode, bucket: str) -> None:
        self.hass = hass
        self._config = config
        self._bucket = bucket
        self._activities = ENERGY_BUCKETS[bucket]
        self._attr_translation_key = f"energy_{bucket}"
        self._attr_unique_id = f"ebusd_energy_{bucket}_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_value: float = 0.0
        self._power_kw: float | None = None
        self._last_sample = None
        self._init_activity()

    @property
    def available(self) -> bool:
        return self._config.power is not None

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                self._attr_native_value = float(last.native_value or 0)
            except TypeError, ValueError:
                self._attr_native_value = 0.0
        await self._subscribe_activity()
        await self._subscribe_extra()

    async def _subscribe_extra(self) -> None:
        power = self._config.power
        if power is None or "power" in self._subscribed:
            return
        self._subscribed.add("power")

        @callback
        def _handle_power(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            try:
                value = float(_get(payload, power.field))
            except TypeError, ValueError:
                return
            self._accumulate()
            self._power_kw = value * self._config.power_factor

        self._unsubscribe.append(
            await mqtt.async_subscribe(self.hass, power.read_topic, _handle_power)
        )

    @callback
    def _accumulate(self) -> None:
        """Book the energy since the last sample to the bucket of the previous activity."""
        now = dt_util.utcnow()
        last, self._last_sample = self._last_sample, now
        if last is None or self._power_kw is None or self._activity not in self._activities:
            return
        seconds = (now - last).total_seconds()
        if seconds <= 0 or seconds > _MAX_GAP_SECONDS:
            return
        self._attr_native_value = round(
            self._attr_native_value + self._power_kw * seconds / 3600, 6
        )
        self.async_write_ha_state()

    @callback
    def _on_activity(self, activity: str | None) -> None:
        if activity != self._activity:
            self._accumulate()
            self._activity = activity
