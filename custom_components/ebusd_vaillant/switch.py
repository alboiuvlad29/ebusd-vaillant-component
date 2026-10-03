"""Switch entities for ebusd Vaillant away mode and hot water boost."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_AWAY_MODE_DURATION,
    CONF_QUICK_VETO_DURATION,
    CONF_QUICK_VETO_TEMP,
    DEFAULT_AWAY_MODE_DURATION,
    DEFAULT_QUICK_VETO_DURATION,
    DEFAULT_QUICK_VETO_TEMP,
    DOMAIN,
)
from .coordinator import EbusdCoordinator
from .device import LegacyObjectIdMixin, build_device_info
from .discovery import (
    DiscoveredClimate,
    DiscoveredControl,
    DiscoveredWaterHeater,
    TopicConfig,
    _get,
    _parse_flag,
)

_LOGGER = logging.getLogger(__name__)

_HOLIDAY_RESET = "01.01.2015"
_DATE_FMT = "%d.%m.%Y"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities_by_key: dict[str, _FollowsDiscovery] = {}
    away_duration = entry.options.get(CONF_AWAY_MODE_DURATION, DEFAULT_AWAY_MODE_DURATION)
    quick_veto_duration = entry.options.get(CONF_QUICK_VETO_DURATION, DEFAULT_QUICK_VETO_DURATION)
    quick_veto_temp = entry.options.get(CONF_QUICK_VETO_TEMP, DEFAULT_QUICK_VETO_TEMP)

    def _on_discover(entities: list) -> None:
        new = []

        def _add_or_update(key: str, factory: Any, config: Any) -> None:
            if key in entities_by_key:
                hass.async_create_task(entities_by_key[key].async_update_config(config))
                return
            entity = factory()
            entities_by_key[key] = entity
            new.append(entity)

        for e in entities:
            if isinstance(e, DiscoveredClimate):
                _add_or_update(
                    f"{e.key}_away_mode",
                    lambda e=e: EbusdAwayModeSwitch(hass, e, coordinator, away_duration),
                    e,
                )
                if e.has_quick_veto:
                    _add_or_update(
                        f"{e.key}_quick_veto",
                        lambda e=e: EbusdQuickVetoSwitch(
                            hass, e, coordinator, quick_veto_temp, quick_veto_duration
                        ),
                        e,
                    )
            elif isinstance(e, DiscoveredControl) and e.kind == "switch":
                _add_or_update(
                    f"{e.key}_control", lambda e=e: EbusdControlSwitch(hass, e, coordinator), e
                )
            elif isinstance(e, DiscoveredWaterHeater):
                if e.holiday_start and e.holiday_end:
                    _add_or_update(
                        f"{e.key}_away_mode",
                        lambda e=e: EbusdHwcAwayModeSwitch(hass, e, coordinator, away_duration),
                        e,
                    )
                if e.sf_mode:
                    _add_or_update(
                        f"{e.key}_boost", lambda e=e: EbusdHwcBoostSwitch(hass, e, coordinator), e
                    )
        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


class _FollowsDiscovery(LegacyObjectIdMixin, SwitchEntity):
    """Base: subscribe per role, seed from the coordinator cache, follow later discovery.

    Discovery can bring topics after the switch was created (e.g. Z{n}SFMode arriving
    after the zone's OpMode on startup); async_update_config rebinds to them.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config: Any, coordinator: EbusdCoordinator) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._attr_device_info = build_device_info(config)
        # role -> (read topic, unsubscribe callback)
        self._subscriptions: dict[str, tuple[str, Any]] = {}

    def _bindings(self) -> dict[str, tuple[TopicConfig | None, Any]]:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await self._rebind()

    async def async_update_config(self, config: Any) -> None:
        self._config = config
        if self.platform is not None:
            await self._rebind()
            self.async_write_ha_state()

    async def _rebind(self) -> None:
        for role, (topic_cfg, handler) in self._bindings().items():
            topic = topic_cfg.read_topic if topic_cfg else None
            current = self._subscriptions.get(role)
            if current and current[0] == topic:
                continue
            if current:
                current[1]()
                del self._subscriptions[role]
            if topic_cfg is None:
                continue
            unsub = await mqtt.async_subscribe(
                self.hass, topic_cfg.read_topic, self._wrap(topic_cfg, handler)
            )
            self._subscriptions[role] = (topic_cfg.read_topic, unsub)
            value = self._coordinator.get_current_value(topic_cfg)
            if value is not None:
                handler(value)
        self._after_update()

    def _wrap(self, topic_cfg: TopicConfig, handler: Any) -> Any:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError, ValueError:
                payload = msg.payload
            value = _get(payload, topic_cfg.field)
            if value is not None:
                handler(value)
                self._after_update()
                self.async_write_ha_state()

        return _handle

    @callback
    def _after_update(self) -> None:
        """Hook run after values changed."""

    async def async_will_remove_from_hass(self) -> None:
        for _topic, unsub in self._subscriptions.values():
            unsub()
        self._subscriptions.clear()

    async def _publish(self, topic: str, payload: str) -> None:
        _LOGGER.debug("MQTT publish: %s -> %s", topic, payload)
        await mqtt.async_publish(self.hass, topic, payload)


class _AwaySwitch(_FollowsDiscovery):
    """Away mode (holiday): start and end dates on ebusd."""

    _attr_icon = "mdi:airplane-takeoff"
    _legacy_object_id = "Away Mode"

    def __init__(
        self, hass: HomeAssistant, config: Any, coordinator: EbusdCoordinator, away_duration: int
    ) -> None:
        super().__init__(hass, config, coordinator)
        self._away_duration = away_duration
        self._attr_unique_id = f"ebusd_away_mode_{config.key}"
        self._holiday_start: str | None = None
        self._holiday_end: str | None = None

    def _bindings(self) -> dict[str, tuple[TopicConfig | None, Any]]:
        return {
            "holiday_start": (self._config.holiday_start, self._handle_holiday_start),
            "holiday_end": (self._config.holiday_end, self._handle_holiday_end),
        }

    @callback
    def _handle_holiday_start(self, value: Any) -> None:
        self._holiday_start = str(value)

    @callback
    def _handle_holiday_end(self, value: Any) -> None:
        self._holiday_end = str(value)

    @property
    def is_on(self) -> bool:
        if not self._holiday_start or not self._holiday_end:
            return False
        try:
            now = datetime.now().date()
            start = datetime.strptime(self._holiday_start, _DATE_FMT).date()
            end = datetime.strptime(self._holiday_end, _DATE_FMT).date()
            return start <= now <= end
        except ValueError:
            return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        today = datetime.now().date()
        start_str = today.strftime(_DATE_FMT)
        end_str = (today + timedelta(days=self._away_duration)).strftime(_DATE_FMT)
        if self._config.holiday_start:
            await self._publish(self._config.holiday_start.write_topic, start_str)
        if self._config.holiday_end:
            await self._publish(self._config.holiday_end.write_topic, end_str)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._config.holiday_start:
            await self._publish(self._config.holiday_start.write_topic, _HOLIDAY_RESET)
        if self._config.holiday_end:
            await self._publish(self._config.holiday_end.write_topic, _HOLIDAY_RESET)


class EbusdAwayModeSwitch(_AwaySwitch):
    """Switch to toggle away mode (holiday) for a heating zone, setting start/end dates on ebusd."""

    _attr_translation_key = "away"


class EbusdHwcAwayModeSwitch(_AwaySwitch):
    """Switch to toggle away mode (holiday) for a hot water circuit, setting dates on ebusd."""

    _attr_translation_key = "hot_water_away"


class EbusdHwcBoostSwitch(_FollowsDiscovery):
    """Switch for a one-time hot water charge (HwcSFMode=load); turns off when it ends."""

    _attr_icon = "mdi:water-boiler-alert"
    _attr_translation_key = "hot_water_boost"
    _legacy_object_id = "Boost"

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredWaterHeater, coordinator: EbusdCoordinator
    ) -> None:
        super().__init__(hass, config, coordinator)
        self._attr_unique_id = f"ebusd_boost_{config.key}"
        self._sf_mode: str | None = None

    def _bindings(self) -> dict[str, tuple[TopicConfig | None, Any]]:
        return {"sf_mode": (self._config.sf_mode, self._handle_sf_mode)}

    @callback
    def _handle_sf_mode(self, value: Any) -> None:
        self._sf_mode = str(value)

    @property
    def is_on(self) -> bool:
        return self._sf_mode == "load"

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._config.sf_mode and self._config.sf_mode.write_topic:
            await self._publish(self._config.sf_mode.write_topic, "load")

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._config.sf_mode and self._config.sf_mode.write_topic:
            await self._publish(self._config.sf_mode.write_topic, "auto")


_DATE_TIME_FMT = "%d.%m.%Y %H:%M:%S"
_QUICK_VETO_CANCEL_DATE = "01.01.2015"
_QUICK_VETO_CANCEL_TIME = "00:00:00"


class EbusdQuickVetoSwitch(_FollowsDiscovery):
    """Heating boost for a zone: Vaillant's quick veto (temperature X for N hours).

    Turns itself off when the boost ends, so HomeKit and dashboards show the right state.
    """

    _attr_icon = "mdi:thermometer-chevron-up"
    _attr_translation_key = "heating_boost"
    _legacy_object_id = "Quick Veto"

    def __init__(
        self,
        hass: HomeAssistant,
        config: DiscoveredClimate,
        coordinator: EbusdCoordinator,
        quick_veto_temp: float,
        quick_veto_duration: int,
    ) -> None:
        super().__init__(hass, config, coordinator)
        self._quick_veto_temp = quick_veto_temp
        self._quick_veto_duration = quick_veto_duration
        self._attr_unique_id = f"ebusd_quick_veto_{config.key}"
        self._quick_veto_end_date: str | None = None
        self._quick_veto_end_time: str | None = None
        self._boost_temperature: float | None = None
        self._boost_duration: float | None = None
        self._sf_mode: str | None = None
        self._cancel_end_timer: Any = None

    def _bindings(self) -> dict[str, tuple[TopicConfig | None, Any]]:
        return {
            "end_date": (self._config.quick_veto_end_date, self._handle_end_date),
            "end_time": (self._config.quick_veto_end_time, self._handle_end_time),
            "temperature": (self._config.quick_veto_temp, self._handle_temperature),
            "duration": (self._config.quick_veto_duration, self._handle_duration),
            "sf_mode": (self._config.sf_mode, self._handle_sf_mode),
        }

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_timer()

    @callback
    def _after_update(self) -> None:
        self._schedule_end()

    @callback
    def _handle_end_date(self, value: Any) -> None:
        self._quick_veto_end_date = str(value)

    @callback
    def _handle_end_time(self, value: Any) -> None:
        self._quick_veto_end_time = str(value)

    @callback
    def _handle_temperature(self, value: Any) -> None:
        try:
            self._boost_temperature = float(value)
        except TypeError, ValueError:
            self._boost_temperature = None

    @callback
    def _handle_sf_mode(self, value: Any) -> None:
        self._sf_mode = str(value)

    @callback
    def _handle_duration(self, value: Any) -> None:
        try:
            self._boost_duration = float(value)
        except TypeError, ValueError:
            self._boost_duration = None

    def _end(self) -> datetime | None:
        if not self._quick_veto_end_date or not self._quick_veto_end_time:
            return None
        try:
            return datetime.strptime(
                f"{self._quick_veto_end_date} {self._quick_veto_end_time}", _DATE_TIME_FMT
            )
        except ValueError:
            return None

    @callback
    def _cancel_timer(self) -> None:
        if self._cancel_end_timer is not None:
            self._cancel_end_timer()
            self._cancel_end_timer = None

    @callback
    def _schedule_end(self) -> None:
        """Write the state again when the boost ends, so it flips to off by itself."""
        self._cancel_timer()
        end = self._end()
        if end is None:
            return
        delay = (end - datetime.now()).total_seconds()
        if delay <= 0:
            return

        @callback
        def _ended(_now: Any) -> None:
            self._cancel_end_timer = None
            self.async_write_ha_state()

        self._cancel_end_timer = async_call_later(self.hass, delay + 1, _ended)

    @property
    def is_on(self) -> bool:
        if self._config.sf_mode is not None and self._sf_mode is not None:
            # newer definitions: Z{n}SFMode = veto while the quick veto runs
            return self._sf_mode == "veto"
        end = self._end()
        return end is not None and end > datetime.now()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        end = self._end()
        active = self.is_on and end is not None and end > datetime.now()
        return {
            "boost_temperature": self._boost_temperature,
            "boost_duration_hours": self._boost_duration,
            "boost_ends_at": end.isoformat() if active else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        qv = self._config.quick_veto_temp
        qd = self._config.quick_veto_duration
        if qv and qv.write_topic:
            await self._publish(qv.write_topic, str(self._quick_veto_temp))
        if qd and qd.write_topic:
            await self._publish(qd.write_topic, str(self._quick_veto_duration))
        else:
            return  # nothing started, so nothing to show
        # Show it on right away; the next SFMode / end date update confirms or corrects it.
        if self._config.sf_mode is not None:
            self._sf_mode = "veto"
        else:
            end = datetime.now() + timedelta(hours=self._quick_veto_duration)
            self._quick_veto_end_date = end.strftime(_DATE_FMT)
            self._quick_veto_end_time = end.strftime("%H:%M:%S")
        self._schedule_end()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        sf_mode = self._config.sf_mode
        if sf_mode is not None and sf_mode.write_topic:
            # newer definitions ignore duration 0 and have read-only end date/time
            await self._publish(sf_mode.write_topic, "auto")
            return
        qd = self._config.quick_veto_duration
        if qd and qd.write_topic:
            await self._publish(qd.write_topic, "0")
        qed = self._config.quick_veto_end_date
        if qed and qed.write_topic:
            await self._publish(qed.write_topic, _QUICK_VETO_CANCEL_DATE)
        qet = self._config.quick_veto_end_time
        if qet and qet.write_topic:
            await self._publish(qet.write_topic, _QUICK_VETO_CANCEL_TIME)


class EbusdControlSwitch(_FollowsDiscovery):
    """A plain on/off register, e.g. Green iQ."""

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredControl, coordinator: EbusdCoordinator
    ) -> None:
        super().__init__(hass, config, coordinator)
        self._attr_translation_key = config.translation_key
        self._attr_unique_id = f"ebusd_control_{config.key}"
        self._legacy_object_id = config.name
        if config.entity_category:
            self._attr_entity_category = EntityCategory(config.entity_category)
        self._state: bool | None = None

    def _bindings(self) -> dict[str, tuple[TopicConfig | None, Any]]:
        return {"state": (self._config.topic, self._handle_state)}

    @callback
    def _handle_state(self, value: Any) -> None:
        self._state = _parse_flag(value)

    @property
    def is_on(self) -> bool | None:
        return self._state

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        if self._config.topic.write_topic:
            await self._publish(self._config.topic.write_topic, "on" if on else "off")
            self._state = on
            self.async_write_ha_state()
