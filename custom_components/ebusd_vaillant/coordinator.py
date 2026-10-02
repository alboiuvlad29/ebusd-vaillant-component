"""Persistent MQTT listener that discovers ebusd entities as topics appear."""

from __future__ import annotations

import json
import logging
from asyncio import Task
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    _DISCOVERY_TOPICS_HC,
    _DISCOVERY_TOPICS_HWC,
    _DISCOVERY_TOPICS_PRESSURE,
    _DISCOVERY_TOPICS_ZONE,
    CONF_COOLING,
    CONF_MAX_ZONES,
    CONF_ZONES_WITH_TEMP_ONLY,
    DEFAULT_COOLING,
    DEFAULT_MANUFACTURER,
    DEFAULT_MAX_ZONES,
    DEFAULT_ZONES_WITH_TEMP_ONLY,
    DISCOVERY_DEVICE_NAMES,
    DOMAIN,
    ESSENTIAL_SENSOR_TOPICS,
    MODE_VOCAB_DAY,
    OLD_DEFINITIONS_URL,
    POLL_PRIMING_ALL,
    POLL_PRIMING_OFF,
    PRIORITY_FAST,
    PRIORITY_SLOW,
    poll_priming,
)
from .discovery import (
    DiscoveredClimate,
    DiscoveredCoolTempLimit,
    DiscoveredEffectiveTarget,
    DiscoveredErrorSensor,
    DiscoveredFlag,
    DiscoveredFlowTempRange,
    DiscoveredOperatingMode,
    DiscoveredPressureMonitor,
    DiscoveredSensor,
    DiscoveredTextSensor,
    DiscoveredWaterHeater,
    TopicConfig,
    _analyze,
    _get,
    discover_manufacturer,
)
from .writes import WriteGuard

_LOGGER = logging.getLogger(__name__)

DiscoveredEntity = DiscoveredClimate | DiscoveredWaterHeater | DiscoveredSensor


def _entity_sig(e: DiscoveredClimate | DiscoveredWaterHeater | DiscoveredSensor) -> tuple:
    """Return a signature tuple that changes when an entity gains new topic configuration."""
    if isinstance(e, DiscoveredClimate):
        return (
            e.name,
            e.target_temperature is not None,
            e.target_temperature_high is not None,
            e.target_temperature_low is not None,
            e.holiday_start_time is not None,
            e.holiday_end_time is not None,
            e.run_data_status is not None,
            e.hc_status is not None,
            e.mode_vocab,
            tuple(e.hvac_modes),
            e.manual_temperature.read_topic if e.manual_temperature else None,
            e.target_temperature.read_topic if e.target_temperature else None,
            e.temp_desired is not None,
            e.zone_status is not None,
            e.sf_mode is not None,
            e.activity.present() if e.activity else (),
        )
    if isinstance(e, DiscoveredWaterHeater):
        return (
            e.name,
            e.holiday_start is not None,
            e.holiday_end is not None,
            e.holiday_start_time is not None,
            e.holiday_end_time is not None,
            e.sf_mode is not None,
            e.mode_vocab,
        )
    if isinstance(e, DiscoveredEffectiveTarget):
        z = e.zone
        return (
            e.key,
            *(
                cfg.read_topic if cfg else None
                for cfg in (z.temp_desired, z.manual_temperature, z.mode, z.sf_mode)
            ),
            z.quick_veto_temp.read_topic if z.quick_veto_temp else None,
        )
    if isinstance(e, DiscoveredPressureMonitor):
        return (
            e.key,
            (e.pressure.read_topic, e.pressure.field) if e.pressure else None,
            e.pressure_loss is not None,
        )
    if isinstance(e, DiscoveredOperatingMode):
        return (e.key, e.activity.present(), e.power.read_topic if e.power else None)
    return (type(e).__name__, e.key)


Listener = Callable[[list[DiscoveredEntity]], None]


class EbusdCoordinator:
    """Subscribes to ebusd/# and notifies listeners whenever new entities are discovered."""

    def __init__(
        self, hass: HomeAssistant, prefix: str, display_name: str, entry: ConfigEntry
    ) -> None:
        self._hass = hass
        self._prefix = prefix
        self._display_name = display_name
        self._entry = entry
        self._by_device: dict[str, dict[str, Any]] = {}
        self._listeners: list[Listener] = []
        self._known_entity_sigs: frozenset[tuple] = frozenset()
        self._unsub: Callable | None = None
        self._bg_tasks: list[Task] = []
        self._stopping: bool = False
        self._writes = WriteGuard(hass)

    async def async_start(self) -> None:
        self._unsub = await mqtt.async_subscribe(
            self._hass, f"{self._prefix}/#", self._handle_message
        )
        _LOGGER.debug("ebusd coordinator: listening on %s/#", self._prefix)
        if poll_priming(self._entry.options) != POLL_PRIMING_OFF:
            self._schedule_task(self._discovery_prime(), "ebusd discovery prime")

    async def _discovery_prime(self) -> None:
        """Publish ?1 to minimal discovery topics for common device names.

        Triggers ebusd to publish the few values needed for _analyze() to
        discover entities.  Unknown devices/topics are silently ignored.
        Once entities are discovered, _prime_values() handles the full set.
        """
        max_zones = self._entry.options.get(CONF_MAX_ZONES, DEFAULT_MAX_ZONES)
        topics: list[str] = [
            *[
                self._prefix + "/" + dev + "/" + t
                for dev in DISCOVERY_DEVICE_NAMES
                for t in _DISCOVERY_TOPICS_HWC
            ],
            *[
                self._prefix + "/" + dev + "/" + t
                for dev in DISCOVERY_DEVICE_NAMES
                for t in _DISCOVERY_TOPICS_PRESSURE
            ],
            *[
                self._prefix + "/" + dev + "/" + t.format(n=z)
                for dev in DISCOVERY_DEVICE_NAMES
                for z in range(1, max_zones + 1)
                for t in _DISCOVERY_TOPICS_ZONE
            ],
            *[
                self._prefix + "/" + dev + "/" + t.format(n=hc)
                for dev in DISCOVERY_DEVICE_NAMES
                for hc in range(1, max_zones + 1)
                for t in _DISCOVERY_TOPICS_HC
            ],
        ]
        _LOGGER.info("Priming discovery: sending %d get requests", len(topics))
        for topic in topics:
            if self._stopping:
                return
            await mqtt.async_publish(self._hass, topic + "/get", "?1")

    async def async_write_setpoint(
        self, topic_cfg: TopicConfig, payload: str, skip_unchanged: bool = True
    ) -> None:
        """Write a setpoint through the EEPROM write guard (see writes.py)."""
        if topic_cfg.write_topic is None:
            return
        current = self.get_current_value(topic_cfg) if skip_unchanged else None
        await self._writes.async_write(topic_cfg.write_topic, payload, current)

    def async_stop(self) -> None:
        self._writes.async_stop()
        self._stopping = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        for task in self._bg_tasks:
            task.cancel()
        self._bg_tasks.clear()

    def _schedule_task(self, coro, name: str) -> None:
        task = self._hass.async_create_background_task(coro, name)
        self._bg_tasks.append(task)

    @property
    def manufacturer(self) -> str:
        """Return the discovered manufacturer name, or the default fallback."""
        return discover_manufacturer(self._by_device) or DEFAULT_MANUFACTURER

    @property
    def mqtt_values(self) -> dict[str, dict[str, Any]]:
        return dict(self._by_device)

    def get_current_value(self, topic_cfg: TopicConfig) -> Any:
        """Return the cached value for a topic config, or None if not yet received."""
        parts = topic_cfg.read_topic.split("/")
        if len(parts) < 3:
            return None
        device, msg = parts[1], parts[2]
        payload = self._by_device.get(device, {}).get(msg)
        return _get(payload, topic_cfg.field)

    def add_listener(self, listener: Listener) -> None:
        """Register a listener. Fires immediately with current state, then on each new discovery."""
        self._listeners.append(listener)
        entities = self._analyze()
        if entities:
            listener(entities)
            if poll_priming(self._entry.options) != POLL_PRIMING_OFF:
                self._schedule_task(self._prime_values(entities), "ebusd prime values")

    def discovered_entities(self) -> list[DiscoveredEntity]:
        """The entities discovery currently derives from the cached messages."""
        return self._analyze()

    @callback
    def _check_old_definitions(self, entities: list[DiscoveredEntity]) -> None:
        """Suggest the newer ebusd definitions when a zone still uses Z{n}DayTemp."""
        old = sorted(
            e.name
            for e in entities
            if isinstance(e, DiscoveredClimate)
            and e.manual_temperature is not None
            and e.manual_temperature.read_topic.endswith("DayTemp")
            and e.mode_vocab == MODE_VOCAB_DAY
        )
        issue_id = f"old_definitions_{self._prefix}"
        if old:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                learn_more_url=OLD_DEFINITIONS_URL,
                translation_key="old_definitions",
                translation_placeholders={"zones": ", ".join(old)},
            )
        else:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)

    def _analyze(self) -> list[DiscoveredEntity]:
        options = self._entry.options
        return _analyze(
            self._by_device,
            self._prefix,
            self._display_name,
            max_zones=options.get(CONF_MAX_ZONES, DEFAULT_MAX_ZONES),
            zones_with_temp_only=options.get(
                CONF_ZONES_WITH_TEMP_ONLY, DEFAULT_ZONES_WITH_TEMP_ONLY
            ),
            cooling_mode=options.get(CONF_COOLING, DEFAULT_COOLING),
        )

    def _collect_read_topics(self, entities: list[DiscoveredEntity]) -> dict[str, bool]:
        """Map each read topic the entities use to whether it is essential (fast poll).

        The heat pump activity messages (hmu Status00/01/07) are left out: ebusd
        overhears them on the bus, polling them would only add traffic.
        """
        topics: dict[str, bool] = {}

        def add(cfgs: list, essential: bool) -> None:
            for cfg in cfgs:
                if cfg is not None:
                    topics[cfg.read_topic] = topics.get(cfg.read_topic, False) or essential

        for entity in entities:
            if isinstance(entity, DiscoveredSensor):
                name = entity.topic.read_topic.rsplit("/", 1)[-1]
                add([entity.topic], name in ESSENTIAL_SENSOR_TOPICS)
            elif isinstance(entity, DiscoveredWaterHeater):
                add(
                    [
                        entity.mode,
                        entity.target_temperature,
                        entity.current_temperature,
                        entity.sf_mode,
                    ],
                    True,
                )
                add(
                    [
                        entity.holiday_start,
                        entity.holiday_end,
                        entity.holiday_start_time,
                        entity.holiday_end_time,
                    ],
                    False,
                )
            elif isinstance(entity, DiscoveredFlowTempRange):
                add(
                    [
                        entity.min_flow_temp,
                        entity.max_flow_temp,
                        entity.current_flow_temp,
                        entity.run_data_status,
                    ],
                    False,
                )
            elif isinstance(entity, DiscoveredCoolTempLimit):
                add([entity.cool_temp, entity.run_data_status], False)
            elif isinstance(entity, DiscoveredErrorSensor | DiscoveredFlag | DiscoveredTextSensor):
                add([entity.topic], False)
            elif isinstance(entity, DiscoveredEffectiveTarget):
                continue  # all of its topics belong to the zone's climate entity
            elif isinstance(entity, DiscoveredPressureMonitor):
                continue  # its topics come from the pressure sensor or overheard Status07
            elif isinstance(entity, DiscoveredOperatingMode):
                # Status messages are overheard; the power input is primed with the sensors
                add([entity.power], True)
            else:  # DiscoveredClimate
                add(
                    [
                        entity.mode,
                        entity.current_temperature,
                        entity.target_temperature,
                        entity.target_temperature_high,
                        entity.target_temperature_low,
                        entity.manual_temperature,
                        entity.temp_desired,
                        entity.quick_veto_temp,
                        entity.quick_veto_duration,
                        entity.sf_mode,
                    ],
                    True,
                )
                add(
                    [
                        entity.holiday_start,
                        entity.holiday_end,
                        entity.holiday_start_time,
                        entity.holiday_end_time,
                        entity.quick_veto_end_date,
                        entity.quick_veto_end_time,
                        entity.run_data_status,
                        entity.hc_status,
                        entity.zone_status,
                    ],
                    False,
                )
        return topics

    def _priming_plan(self, entities: list[DiscoveredEntity]) -> list[tuple[str, str]]:
        """(read topic, priority) pairs to publish, essentials first."""
        mode = poll_priming(self._entry.options)
        if mode == POLL_PRIMING_OFF:
            return []
        topics = self._collect_read_topics(entities)
        plan = [
            (topic, PRIORITY_FAST if essential or mode == POLL_PRIMING_ALL else PRIORITY_SLOW)
            for topic, essential in topics.items()
        ]
        plan.sort(key=lambda item: (item[1] != PRIORITY_FAST, item[0]))
        return plan

    async def _prime_values(self, entities: list[DiscoveredEntity]) -> None:
        """Publish ?1/?5 to /get topics so ebusd keeps polling the values in use."""
        for topic, priority in self._priming_plan(entities):
            if self._stopping:
                return
            get_topic = f"{topic}/get"
            _LOGGER.debug("Priming value: %s %s", get_topic, priority)
            await mqtt.async_publish(self._hass, get_topic, priority)

    @callback
    def _handle_message(self, msg: mqtt.ReceiveMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) < 3:
            return
        device, msg_name = parts[1], parts[2]
        # .../get and .../set are requests (our own writes included), not values: ebusd
        # republishes the value topic itself once a write succeeded.
        if device in ("global", "Broadcast") or msg.topic.endswith(("/get", "/set")):
            return

        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError, ValueError:
            payload = msg.payload

        device_msgs = self._by_device.setdefault(device, {})
        if device_msgs.get(msg_name) == payload:
            return  # unchanged  -  skip re-analysis

        device_msgs[msg_name] = payload
        entities = self._analyze()
        new_sigs = frozenset(_entity_sig(e) for e in entities)
        if new_sigs == self._known_entity_sigs:
            return  # no change in entity set or config  -  skip listener calls
        self._known_entity_sigs = new_sigs
        self._check_old_definitions(entities)
        _LOGGER.debug("Discovered entities: %s", sorted(e.name for e in entities))
        for listener in self._listeners:
            listener(entities)
        if poll_priming(self._entry.options) != POLL_PRIMING_OFF:
            self._schedule_task(self._prime_values(entities), "ebusd prime values")
