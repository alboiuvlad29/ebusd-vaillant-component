"""Binary sensors for ebusd Vaillant: low system pressure and eBUS connection."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
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
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LOW_PRESSURE,
    CONF_MQTT_PREFIX,
    DEFAULT_LOW_PRESSURE,
    DEFAULT_MQTT_PREFIX,
    DOMAIN,
)
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import (
    DiscoveredFlag,
    DiscoveredNoiseSchedule,
    DiscoveredPressureMonitor,
    TopicConfig,
    _get,
)
from .noise import NoiseSchedule

# SilentTimer_<Day> holds one slot at a time (the slot index is an input of the read), so the
# slots are requested one by one, spaced out to keep the bus quiet.
NOISE_REQUEST_INTERVAL = 1.0
NOISE_REFRESH = timedelta(hours=1)
NOISE_DEFAULT_SLOTS = 3
_DAY_MESSAGE = re.compile(r"^SilentTimer_[A-Za-z]+$")

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
    monitors: dict[str, EbusdLowPressureBinarySensor] = {}
    noise: dict[str, EbusdNoiseReductionBinarySensor] = {}

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredPressureMonitor):
                if e.key in monitors:
                    hass.async_create_task(monitors[e.key].async_update_config(e))
                    continue
                monitors[e.key] = EbusdLowPressureBinarySensor(hass, e, threshold)
                new.append(monitors[e.key])
            elif isinstance(e, DiscoveredNoiseSchedule):
                if e.key in noise:
                    hass.async_create_task(noise[e.key].async_update_config(e))
                    continue
                noise[e.key] = EbusdNoiseReductionBinarySensor(hass, e)
                new.append(noise[e.key])
            elif isinstance(e, DiscoveredFlag) and e.key not in seen:
                seen.add(e.key)
                new.append(EbusdFlagBinarySensor(hass, e))
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
        # role -> ((read topic, field), unsubscribe callback)
        self._subscriptions: dict[str, tuple[tuple[str, str], Any]] = {}

    async def async_added_to_hass(self) -> None:
        await self._bind(self._config)

    async def async_update_config(self, config: DiscoveredPressureMonitor) -> None:
        """Follow better sources found later (e.g. Status07 after the polled pressure)."""
        self._config = config
        if self.platform is not None:
            await self._bind(config)
            self.async_write_ha_state()

    async def _bind(self, config: DiscoveredPressureMonitor) -> None:
        for role, cfg, handler in (
            ("pressure", config.pressure, self._handle_pressure),
            ("pressure_loss", config.pressure_loss, self._handle_loss),
        ):
            source = (cfg.read_topic, cfg.field) if cfg else None
            current = self._subscriptions.get(role)
            if current and current[0] == source:
                continue
            if current:
                current[1]()
                del self._subscriptions[role]
            if cfg is None:
                continue
            self._subscriptions[role] = (source, await self._subscribe(cfg, handler))

    async def async_will_remove_from_hass(self) -> None:
        for _source, unsub in self._subscriptions.values():
            unsub()
        self._subscriptions.clear()

    async def _subscribe(self, topic_cfg: TopicConfig, handler: Any) -> Any:
        @callback
        def _wrap(msg: mqtt.ReceiveMessage) -> None:
            value = _get(_payload(msg.payload), topic_cfg.field)
            if value is not None:
                handler(value)
                self.async_write_ha_state()

        return await mqtt.async_subscribe(self.hass, topic_cfg.read_topic, _wrap)

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


class EbusdFlagBinarySensor(BinarySensorEntity):
    """An on/off value of a zone, such as whether a schedule time slot is active."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: DiscoveredFlag) -> None:
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


class EbusdNoiseReductionBinarySensor(BinarySensorEntity):
    """On while a noise reduction period of the schedule (SilentTimer) is running.

    The controller has no separate on/off: noise reduction is active whenever the
    current time falls into one of the day's periods.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "noise_reduction_active"
    _attr_icon = "mdi:volume-off"

    def __init__(self, hass: HomeAssistant, config: DiscoveredNoiseSchedule) -> None:
        self.hass = hass
        self._config = config
        self._attr_unique_id = f"ebusd_noise_reduction_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._schedule = NoiseSchedule()
        self._subscribed: dict[str, Any] = {}
        self._cancel_tick: Any = None
        self._cancel_refresh: Any = None
        self._request_task: asyncio.Task | None = None
        self._request_again = False

    async def async_added_to_hass(self) -> None:
        await self._bind()
        self._request_slots_soon()

        @callback
        def _refresh(_now: Any) -> None:
            self._request_slots_soon()

        self._cancel_refresh = async_track_time_interval(self.hass, _refresh, NOISE_REFRESH)

        # the state depends on the clock as well: re-evaluate every minute
        @callback
        def _tick(_now: Any) -> None:
            self.async_write_ha_state()

        self._cancel_tick = async_track_time_interval(self.hass, _tick, timedelta(minutes=1))

    async def async_update_config(self, config: DiscoveredNoiseSchedule) -> None:
        self._config = config
        if self.platform is not None:
            known = set(self._subscribed)
            await self._bind()
            if set(self._subscribed) != known:
                self._request_slots_soon()
            self.async_write_ha_state()

    @callback
    def _request_slots_soon(self) -> None:
        if self._request_task and not self._request_task.done():
            self._request_again = True  # new days appeared while a pass was running
            return
        self._request_task = self.hass.async_create_background_task(
            self._request_slots(), "ebusd noise reduction slots"
        )

    async def _request_slots(self) -> None:
        self._request_again = True
        while self._request_again:
            self._request_again = False
            await self._request_pass()

    async def _request_pass(self) -> None:
        """Read slot 0, then the other slots the day has, of every SilentTimer_<Day>."""
        first = True
        for cfg in self._config.topics:
            message = cfg.read_topic.rsplit("/", 1)[-1]
            if not _DAY_MESSAGE.match(message):
                continue  # fixed-slot messages (SilentTimer_Monday0) need no request
            if not first:
                await asyncio.sleep(NOISE_REQUEST_INTERVAL)
            first = False
            await mqtt.async_publish(self.hass, f"{cfg.read_topic}/get", "0")
            await asyncio.sleep(NOISE_REQUEST_INTERVAL)  # the answer carries the slot count
            count = self._schedule.slot_count(message) or NOISE_DEFAULT_SLOTS
            for index in range(1, count):
                await mqtt.async_publish(self.hass, f"{cfg.read_topic}/get", str(index))
                if index < count - 1:
                    await asyncio.sleep(NOISE_REQUEST_INTERVAL)

    async def _bind(self) -> None:
        for cfg in self._config.topics:
            if cfg.read_topic in self._subscribed:
                continue
            self._subscribed[cfg.read_topic] = await mqtt.async_subscribe(
                self.hass, cfg.read_topic, self._handler(cfg.read_topic.rsplit("/", 1)[-1])
            )
            for payload in (self._peek(cfg),):
                if payload is not None:
                    self._schedule.update(cfg.read_topic.rsplit("/", 1)[-1], payload)

    def _peek(self, cfg: TopicConfig) -> Any:
        coordinator = self.hass.data.get(DOMAIN, {})
        for coord in coordinator.values():
            if isinstance(coord, EbusdCoordinator):
                return coord.get_current_value(cfg)
        return None

    def _handler(self, message: str) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            self._schedule.update(message, _payload(msg.payload))
            self.async_write_ha_state()

        return _handle

    @property
    def available(self) -> bool:
        return self._schedule.known

    @property
    def is_on(self) -> bool | None:
        return self._schedule.active_at(dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"schedule": self._schedule.as_dict()}

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_tick:
            self._cancel_tick()
        if self._cancel_refresh:
            self._cancel_refresh()
        if self._request_task:
            self._request_task.cancel()
        for unsub in self._subscribed.values():
            unsub()
        self._subscribed.clear()
