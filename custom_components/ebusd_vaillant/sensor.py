"""Sensor entities for ebusd Vaillant."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
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
from .const import (
    CONF_ALLOW_INSTALLER,
    DEFAULT_ALLOW_INSTALLER,
    DOMAIN,
    FAULT_EVENT,
)
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import (
    DiscoveredControl,
    DiscoveredEffectiveTarget,
    DiscoveredErrorSensor,
    DiscoveredFaultHistory,
    DiscoveredOperatingMode,
    DiscoveredOutdoorTemp,
    DiscoveredSensor,
    DiscoveredTextSensor,
    TopicConfig,
    _get,
)
from .faults import FaultEntry, parse_fault_entry

# Pause between the FaultHistory requests, so the bus is not flooded
FAULT_REQUEST_INTERVAL = 1.0
# The broadcast outside temperature is trusted for this long before the controller value is used
OUTDOOR_BROADCAST_MAX_AGE = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()
    followers: dict[str, list[_ActivityFollower]] = {}
    targets: dict[str, EbusdEffectiveTargetSensor] = {}
    outdoor: dict[str, EbusdOutdoorTempSensor] = {}
    allow_installer = entry.options.get(CONF_ALLOW_INSTALLER, DEFAULT_ALLOW_INSTALLER)

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdSensor(hass, e))
            elif isinstance(e, DiscoveredEffectiveTarget):
                if e.key in targets:
                    hass.async_create_task(targets[e.key].async_update_config(e))
                    continue
                targets[e.key] = EbusdEffectiveTargetSensor(hass, e, coordinator)
                new.append(targets[e.key])
            elif isinstance(e, DiscoveredTextSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdTextSensor(hass, e))
            elif isinstance(e, DiscoveredErrorSensor) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdErrorSensor(hass, e))
            elif isinstance(e, DiscoveredFaultHistory) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdLastFaultSensor(hass, e, coordinator))
            elif isinstance(e, DiscoveredOutdoorTemp):
                if e.key in outdoor:
                    hass.async_create_task(outdoor[e.key].async_update_config(e))
                    continue
                outdoor[e.key] = EbusdOutdoorTempSensor(hass, e)
                new.append(outdoor[e.key])
            elif (
                isinstance(e, DiscoveredControl)
                and e.kind == "number"
                and e.installer
                and not allow_installer
                and e.key not in seen
            ):
                seen.add(e.key)
                new.append(EbusdInstallerValueSensor(hass, e))
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
        if config.translation_key:
            self._attr_translation_key = config.translation_key
            if config.translation_placeholders:
                self._attr_translation_placeholders = config.translation_placeholders
        else:
            self._attr_name = config.name
        self._attr_unique_id = f"{config.unique_id_prefix}_{config.key}"
        self._attr_device_class = (
            SensorDeviceClass(config.device_class) if config.device_class else None
        )
        self._attr_state_class = SensorStateClass(config.state_class)
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_info = build_device_info(config)
        if config.entity_category:
            self._attr_entity_category = EntityCategory(config.entity_category)
        self._attr_entity_registry_enabled_default = config.enabled_default


class EbusdInstallerValueSensor(_EbusdNumericSensor):
    """An installer value shown read-only (writable only with "Allow installer settings")."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, config: DiscoveredControl) -> None:
        super().__init__(hass, config.topic.read_topic, config.topic.field)
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_installer_{config.key}"
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_class = (
            SensorDeviceClass(config.device_class) if config.device_class else None
        )
        self._attr_device_info = build_device_info(config)


class EbusdOutdoorTempSensor(SensorEntity):
    """Outside temperature: the passive broadcast (about every minute), else the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "outside_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, hass: HomeAssistant, config: DiscoveredOutdoorTemp) -> None:
        self.hass = hass
        self._config = config
        self._attr_unique_id = f"ebusd_outside_temperature_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_native_value: float | None = None
        self._broadcast: float | None = None
        self._broadcast_at = None
        self._controller: float | None = None
        self._subscribed: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        await self._bind()

    async def async_update_config(self, config: DiscoveredOutdoorTemp) -> None:
        self._config = config
        if self.platform is not None:
            await self._bind()
            self.async_write_ha_state()

    async def _bind(self) -> None:
        for role, cfg in (
            ("broadcast", self._config.broadcast),
            ("controller", self._config.controller),
        ):
            if cfg is None or role in self._subscribed:
                continue
            self._subscribed[role] = await mqtt.async_subscribe(
                self.hass, cfg.read_topic, self._handler(role, cfg.field)
            )

    def _handler(self, role: str, field: str) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            try:
                value = float(_get(payload, field))
            except TypeError, ValueError:
                return
            if role == "broadcast":
                self._broadcast, self._broadcast_at = value, dt_util.utcnow()
            else:
                self._controller = value
            self._attr_native_value = self._compute()
            self.async_write_ha_state()

        return _handle

    def _compute(self) -> float | None:
        fresh = (
            self._broadcast is not None
            and self._broadcast_at is not None
            and dt_util.utcnow() - self._broadcast_at < OUTDOOR_BROADCAST_MAX_AGE
        )
        return self._broadcast if fresh else self._controller

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._subscribed.values():
            unsub()
        self._subscribed.clear()


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
    _attr_entity_category = EntityCategory.DIAGNOSTIC
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


class EbusdTextSensor(SensorEntity):
    """A value shown as text (status, weekday, time)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: DiscoveredTextSensor) -> None:
        self.hass = hass
        self._config = config
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_text_{config.key}"
        self._attr_device_info = build_device_info(config)
        if config.entity_category:
            self._attr_entity_category = EntityCategory(config.entity_category)
        self._attr_native_value: str | None = None
        self._unsubscribe: Any = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, self._config.topic.field)
            if value is not None:
                self._attr_native_value = str(value)
                self.async_write_ha_state()

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self._config.topic.read_topic, _handle
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()


_MANUAL_MODES = frozenset({"manual", "day"})


class EbusdEffectiveTargetSensor(SensorEntity):
    """The temperature a zone is aiming for now: boost, schedule or manual setpoint."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "effective_target_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredEffectiveTarget, coordinator: EbusdCoordinator
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        # same unique ID as the plain Z{n}TempDesired sensor of 1.9.0
        self._attr_unique_id = f"ebusd_zone_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._values: dict[str, Any] = {}
        # role -> (read topic, unsubscribe callback)
        self._subscriptions: dict[str, tuple[str, Any]] = {}

    def _sources(self) -> dict[str, Any]:
        zone = self._config.zone
        return {
            "desired": zone.temp_desired,
            "manual": zone.manual_temperature,
            "mode": zone.mode,
            "sf_mode": zone.sf_mode,
            "veto_temp": zone.quick_veto_temp,
        }

    async def async_added_to_hass(self) -> None:
        await self._bind()

    async def async_update_config(self, config: DiscoveredEffectiveTarget) -> None:
        self._config = config
        if self.platform is not None:
            await self._bind()
            self.async_write_ha_state()

    async def _bind(self) -> None:
        for role, cfg in self._sources().items():
            topic = cfg.read_topic if cfg else None
            current = self._subscriptions.get(role)
            if current and current[0] == topic:
                continue
            if current:
                current[1]()
                del self._subscriptions[role]
                self._values.pop(role, None)
            if cfg is None:
                continue
            unsub = await mqtt.async_subscribe(
                self.hass, cfg.read_topic, self._handler(role, cfg.field)
            )
            self._subscriptions[role] = (cfg.read_topic, unsub)
            value = self._coordinator.get_current_value(cfg)
            if value is not None:
                self._values[role] = value
        self._attr_native_value = self._compute()

    def _handler(self, role: str, field: str) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, field)
            if value is None:
                return
            self._values[role] = value
            self._attr_native_value = self._compute()
            self.async_write_ha_state()

        return _handle

    def _number(self, role: str) -> float | None:
        try:
            value = float(self._values.get(role))
        except TypeError, ValueError:
            return None
        return value if value > 0 else None

    def _compute(self) -> float | None:
        if str(self._values.get("sf_mode")) == "veto" and self._number("veto_temp") is not None:
            return self._number("veto_temp")
        if (desired := self._number("desired")) is not None:
            return desired
        if str(self._values.get("mode")) in _MANUAL_MODES:
            return self._number("manual")
        return None

    async def async_will_remove_from_hass(self) -> None:
        for _topic, unsub in self._subscriptions.values():
            unsub()
        self._subscriptions.clear()


class EbusdLastFaultSensor(SensorEntity):
    """Most recent heat pump fault (F.xx) and the stored fault history.

    LastError is polled by ebusd; FaultHistory0..9 only exist once requested, so the
    sensor asks for them at startup and whenever LastError changes. A fault newer than
    the last one seen (kept across restarts) fires the ebusd_vaillant_fault event and
    raises a Repairs issue.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "last_fault"
    _attr_icon = "mdi:alert-octagon-outline"

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredFaultHistory, coordinator: EbusdCoordinator
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._attr_unique_id = f"ebusd_last_fault_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._store: Store = Store(hass, 1, f"{DOMAIN}.fault_{config.key}")
        self._seen: Any = None  # timestamp of the newest fault already announced
        self._newest: FaultEntry | None = None  # newest entry reported by LastError
        self._slots: dict[int, FaultEntry] = {}
        self._last_raw: Any = None
        self._first = True
        self._request_task: asyncio.Task | None = None
        self._unsubscribe: list[Any] = []

    async def async_added_to_hass(self) -> None:
        if (data := await self._store.async_load()) and data.get("last_seen"):
            self._seen = dt_util.parse_datetime(data["last_seen"])
        for index, cfg in enumerate(self._config.history):
            self._seed_slot(index, cfg)
            self._unsubscribe.append(
                await mqtt.async_subscribe(self.hass, cfg.read_topic, self._slot_handler(index))
            )
        cfg = self._config.last_error
        self._unsubscribe.append(
            await mqtt.async_subscribe(self.hass, cfg.read_topic, self._last_error_handler())
        )
        if (payload := self._coordinator.get_current_value(cfg)) is not None:
            self._on_last_error(payload)

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()
        self._unsubscribe.clear()
        if self._request_task:
            self._request_task.cancel()

    def _seed_slot(self, index: int, cfg: TopicConfig) -> None:
        if (payload := self._coordinator.get_current_value(cfg)) is not None:
            self._set_slot(index, payload)

    def _slot_handler(self, index: int) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = None
            self._set_slot(index, payload)
            self.async_write_ha_state()

        return _handle

    def _last_error_handler(self) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = None
            self._on_last_error(payload)
            self.async_write_ha_state()

        return _handle

    @callback
    def _set_slot(self, index: int, payload: Any) -> None:
        if (entry := parse_fault_entry(payload)) is None:
            self._slots.pop(index, None)
        else:
            self._slots[index] = entry

    @callback
    def _on_last_error(self, payload: Any) -> None:
        changed = self._first or payload != self._last_raw
        self._last_raw = payload
        self._newest = parse_fault_entry(payload)
        self._announce_if_new()
        if changed:
            self._first = False
            self._request_history()

    def _request_history(self) -> None:
        if self._request_task:
            self._request_task.cancel()
        self._request_task = self.hass.async_create_background_task(
            self._request_slots(), "ebusd fault history request"
        )

    async def _request_slots(self) -> None:
        for index, cfg in enumerate(self._config.history):
            if index:
                await asyncio.sleep(FAULT_REQUEST_INTERVAL)
            await mqtt.async_publish(self.hass, f"{cfg.read_topic}/get", "")

    def _entries(self) -> list[FaultEntry]:
        """All known faults, newest first, without duplicates."""
        found = {(e.timestamp, e.code): e for e in self._slots.values()}
        if self._newest is not None:
            found[(self._newest.timestamp, self._newest.code)] = self._newest
        return sorted(found.values(), key=lambda e: e.timestamp, reverse=True)

    @callback
    def _announce_if_new(self) -> None:
        # LastError is by definition the newest entry. The history slots (retained, in any
        # order) only feed the attribute, so they can never announce an old fault.
        latest = self._newest
        if latest is None:
            return
        if self._seen is None:
            # first run: take over what is already there without announcing it
            self._remember(latest)
            return
        if latest.timestamp <= self._seen:
            return
        self._remember(latest)
        self.hass.bus.async_fire(
            FAULT_EVENT,
            {
                "code": latest.label,
                "meaning": latest.meaning,
                "timestamp": latest.timestamp.isoformat(),
                "device": self._config.device_name,
            },
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"heat_pump_fault_{self._config.key}_{int(latest.timestamp.timestamp())}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="heat_pump_fault",
            translation_placeholders={
                "device": self._config.device_name,
                "code": latest.label,
                "meaning": latest.meaning,
                "timestamp": dt_util.as_local(latest.timestamp).strftime("%d.%m.%Y %H:%M"),
            },
        )

    def _remember(self, entry: FaultEntry) -> None:
        self._seen = entry.timestamp
        self._store.async_delay_save(lambda: {"last_seen": entry.timestamp.isoformat()}, 0)

    @property
    def native_value(self) -> str:
        entries = self._entries()
        return entries[0].label if entries else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = self._entries()
        attrs: dict[str, Any] = {"history": [e.as_dict() for e in entries]}
        if entries:
            attrs.update(
                timestamp=entries[0].timestamp.isoformat(),
                status=entries[0].status,
                meaning=entries[0].meaning,
            )
        return attrs
