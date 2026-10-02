"""Climate entities for ebusd Vaillant heating zones."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .activity import (
    ACTIVITY_COOLING,
    ACTIVITY_DEFROST,
    ACTIVITY_HEATING,
    compute_activity,
)
from .const import (
    _STAT_HVAC_ACTION_COOLING,
    _STAT_HVAC_ACTION_HEATING,
    CONF_AWAY_MODE_DURATION,
    CONF_QUICK_VETO_DURATION,
    CONF_QUICK_VETO_TEMP,
    CONF_TEMPERATURE_WRITE,
    DEFAULT_AWAY_MODE_DURATION,
    DEFAULT_QUICK_VETO_DURATION,
    DEFAULT_QUICK_VETO_TEMP,
    DEFAULT_TEMPERATURE_WRITE,
    DOMAIN,
    EBUSD_TO_HA_HVAC,
    HA_TO_EBUSD_HVAC,
    TEMPERATURE_WRITE_SMART,
    ZONE_HVAC_MODES,
)
from .coordinator import EbusdCoordinator
from .device import build_device_info
from .discovery import (
    DiscoveredClimate,
    DiscoveredFlowTempRange,
    TopicConfig,
    _get,
    mode_vocab_from_value,
)
from .services import register_entity, unregister_entity

_LOGGER = logging.getLogger(__name__)

_HA_HVAC_MODE = {
    "auto": HVACMode.AUTO,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "off": HVACMode.OFF,
}

_HOLIDAY_RESET = "01.01.2015"
_DATE_FMT = "%d.%m.%Y"
_TIME_FMT = "%H:%M:%S"
_QUICK_VETO_CANCEL_DATE = "01.01.2015"
_QUICK_VETO_CANCEL_TIME = "00:00:00"
# Z{n}Status / Hc{n}Status values meaning the zone does not ask for heat.
_ZONE_INACTIVE = frozenset({"0", "false", "inactive", "off", "idle", "standby", "none"})
# How long an unconfirmed quick-veto target is shown before falling back.
_PENDING_TARGET_TIMEOUT = timedelta(minutes=10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities_by_name: dict[str, EbusdClimateEntity] = {}
    flow_temp_by_key: dict[str, EbusdFlowTempRangeEntity] = {}
    away_duration = entry.options.get(CONF_AWAY_MODE_DURATION, DEFAULT_AWAY_MODE_DURATION)
    quick_veto_duration = entry.options.get(CONF_QUICK_VETO_DURATION, DEFAULT_QUICK_VETO_DURATION)
    quick_veto_temp = entry.options.get(CONF_QUICK_VETO_TEMP, DEFAULT_QUICK_VETO_TEMP)
    temperature_write = entry.options.get(CONF_TEMPERATURE_WRITE, DEFAULT_TEMPERATURE_WRITE)

    def _on_discover(entities: list) -> None:
        new = []
        for e in entities:
            if isinstance(e, DiscoveredClimate):
                if e.name in entities_by_name:
                    hass.async_create_task(
                        entities_by_name[e.name].async_update_config(e, coordinator)
                    )
                else:
                    entity = EbusdClimateEntity(
                        hass,
                        e,
                        away_duration,
                        quick_veto_duration,
                        quick_veto_temp,
                        coordinator=coordinator,
                        temperature_write=temperature_write,
                    )
                    entities_by_name[e.name] = entity
                    new.append(entity)
            elif isinstance(e, DiscoveredFlowTempRange):
                if e.key not in flow_temp_by_key:
                    entity = EbusdFlowTempRangeEntity(hass, e, coordinator)
                    flow_temp_by_key[e.key] = entity
                    new.append(entity)

        if new:
            async_add_entities(new)

    coordinator.add_listener(_on_discover)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


class EbusdClimateEntity(ClimateEntity):
    """Climate entity for a heating zone: target temperature, HVAC mode, and boost/away presets."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST, PRESET_AWAY]
    _attr_has_entity_name = True
    _attr_should_poll = False
    # Mode names as on the sensoCOMFORT: Time controlled / Manual / Off
    _attr_translation_key = "ebusd_zone"

    def __init__(
        self,
        hass: HomeAssistant,
        config: DiscoveredClimate,
        away_duration: int = DEFAULT_AWAY_MODE_DURATION,
        quick_veto_duration: int = DEFAULT_QUICK_VETO_DURATION,
        quick_veto_temp: float = DEFAULT_QUICK_VETO_TEMP,
        coordinator: EbusdCoordinator | None = None,
        temperature_write: str = DEFAULT_TEMPERATURE_WRITE,
    ) -> None:
        self.hass = hass
        self._config = config
        self._coordinator = coordinator
        self._away_duration = away_duration
        self._quick_veto_duration = quick_veto_duration
        self._quick_veto_temp = quick_veto_temp
        self._temperature_write = temperature_write
        self._attr_name = None  # primary entity of the Zone device; device name is the label
        self._attr_unique_id = f"ebusd_climate_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_min_temp = config.min_temp
        self._attr_max_temp = config.max_temp
        self._attr_target_temperature_step = config.temp_step

        self._attr_hvac_modes = [_HA_HVAC_MODE[m] for m in config.hvac_modes if m in _HA_HVAC_MODE]
        self._mode_vocab = config.mode_vocab
        self._mode_vocab_observed = False
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_action = HVACAction.OFF

        self._attr_current_temperature: float | None = None
        self._attr_target_temperature: float | None = None
        self._attr_target_temperature_high: float | None = None
        self._attr_target_temperature_low: float | None = None

        self._temp_desired: float | None = None
        self._quick_veto_value: float | None = None
        # Optimistic target after a quick veto, until the controller confirms it.
        self._pending_target: float | None = None
        self._pending_since: datetime | None = None
        self._pending_desired: float | None = None

        self._holiday_start: str | None = None
        self._holiday_end: str | None = None
        self._quick_veto_end_date: str | None = None
        self._quick_veto_end_time: str | None = None

        self._run_data_statuscode: str | None = None
        self._hc_statuscode: str | None = None
        self._zone_statuscode: str | None = None
        self._activity_values: dict[str, Any] = {}

        self._attr_supported_features = self._features_for(config)

        # role -> (read topic, unsubscribe callback)
        self._subscriptions: dict[str, tuple[str, Any]] = {}

    @staticmethod
    def _features_for(config: DiscoveredClimate) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.PRESET_MODE
        )
        if config.target_temperature:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if config.target_temperature_high or config.target_temperature_low:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        return features

    def _bindings(self, config: DiscoveredClimate) -> dict[str, tuple[TopicConfig | None, Any]]:
        return {
            "mode": (config.mode, self._handle_mode),
            "current_temperature": (config.current_temperature, self._handle_current_temp),
            "target_temperature": (config.target_temperature, self._handle_target_temp),
            "target_temperature_high": (config.target_temperature_high, self._handle_target_high),
            "target_temperature_low": (config.target_temperature_low, self._handle_target_low),
            "temp_desired": (config.temp_desired, self._handle_temp_desired),
            "holiday_start": (config.holiday_start, self._handle_holiday_start),
            "holiday_end": (config.holiday_end, self._handle_holiday_end),
            "quick_veto_temp": (config.quick_veto_temp, self._handle_quick_veto_temp),
            "quick_veto_end_date": (config.quick_veto_end_date, self._handle_quick_veto_end_date),
            "quick_veto_end_time": (config.quick_veto_end_time, self._handle_quick_veto_end_time),
            "run_data_status": (config.run_data_status, self._handle_run_data_statuscode),
            "hc_status": (config.hc_status, self._handle_hc_statuscode),
            "zone_status": (config.zone_status, self._handle_zone_statuscode),
            **{
                f"activity_{name}": (cfg, self._activity_handler(name))
                for name, cfg in (config.activity.items() if config.activity else [])
                # the status code is already bound as run_data_status
                if name != "statuscode"
            },
        }

    async def _apply_bindings(self, config: DiscoveredClimate, seed: bool) -> None:
        """Subscribe each role to its topic, replacing subscriptions whose topic changed."""
        for role, (topic_cfg, handler) in self._bindings(config).items():
            current = self._subscriptions.get(role)
            topic = topic_cfg.read_topic if topic_cfg else None
            if current and current[0] == topic:
                continue
            if current:
                current[1]()
                del self._subscriptions[role]
            if topic_cfg is None:
                continue
            await self._subscribe(role, topic_cfg, handler)
            if seed and self._coordinator is not None:
                value = self._coordinator.get_current_value(topic_cfg)
                if value is not None:
                    handler(value)

    async def async_added_to_hass(self) -> None:
        await self._apply_bindings(self._config, seed=False)
        register_entity(self.hass, self)

    async def async_will_remove_from_hass(self) -> None:
        unregister_entity(self.hass, self)
        for _topic, unsub in self._subscriptions.values():
            unsub()
        self._subscriptions.clear()

    # --- services (services.py) ---

    async def async_service_set_quick_veto(
        self, temperature: float, duration_hours: float | None = None
    ) -> None:
        if not self._config.quick_veto_temp or not self._config.quick_veto_temp.write_topic:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_quick_veto",
                translation_placeholders={"entity_id": self.entity_id},
            )
        await self._publish_quick_veto(temperature, duration_hours)

    async def async_service_cancel_quick_veto(self) -> None:
        await self._cancel_quick_veto()

    async def async_service_set_away(self, start_date: date, end_date: date) -> None:
        for cfg, day in (
            (self._config.holiday_start, start_date),
            (self._config.holiday_end, end_date),
        ):
            if cfg and cfg.write_topic:
                await self._publish(cfg.write_topic, day.strftime(_DATE_FMT))

    async def async_service_cancel_away(self) -> None:
        for cfg in (self._config.holiday_start, self._config.holiday_end):
            if cfg and cfg.write_topic:
                await self._publish(cfg.write_topic, _HOLIDAY_RESET)

    async def async_update_config(
        self, config: DiscoveredClimate, coordinator: EbusdCoordinator
    ) -> None:
        """Follow a changed discovery result: topics, target layout and HVAC modes."""
        self._coordinator = coordinator
        old_features = self._attr_supported_features
        self._attr_supported_features = self._features_for(config)
        if not config.target_temperature:
            self._attr_target_temperature = None
        if not config.target_temperature_high:
            self._attr_target_temperature_high = None
        if not config.target_temperature_low:
            self._attr_target_temperature_low = None
        await self._apply_bindings(config, seed=True)
        if old_features != self._attr_supported_features:
            _LOGGER.debug("%s: target layout changed", self.entity_id)
        self._config = config
        self._attr_hvac_modes = [_HA_HVAC_MODE[m] for m in config.hvac_modes if m in _HA_HVAC_MODE]
        if self._mode_vocab_observed:
            self._apply_vocab_modes()
        else:
            self._mode_vocab = config.mode_vocab
        self.async_write_ha_state()

    async def _subscribe(self, role: str, topic_cfg: TopicConfig, handler: Any) -> None:
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
        self._subscriptions[role] = (topic_cfg.read_topic, unsub)

    def _activity_handler(self, name: str) -> Any:
        @callback
        def _handle(value: Any) -> None:
            self._activity_values[name] = value
            self._attr_hvac_action = self._determine_hvac_action()

        return _handle

    def _zone_active(self) -> bool | None:
        """Whether this zone is asking for heat (Z{n}Status, else Hc{n}Status); None if unknown."""
        status = self._zone_statuscode or self._hc_statuscode
        if status is None:
            return None
        return status.strip().lower() not in _ZONE_INACTIVE

    @callback
    def _determine_hvac_action(self) -> HVACAction:
        """Derive hvac_action from the heat pump activity and this zone's status."""
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        activity = compute_activity(self._activity_values)
        if activity is not None:
            if activity == ACTIVITY_DEFROST:
                return HVACAction.DEFROSTING
            if activity in (ACTIVITY_HEATING, ACTIVITY_COOLING):
                if self._zone_active() is False:
                    return HVACAction.IDLE
                return HVACAction.HEATING if activity == ACTIVITY_HEATING else HVACAction.COOLING
            # hot water (the heat pump is busy with the cylinder) or idle
            return HVACAction.IDLE

        # Nothing known about the heat pump yet: fall back to the selected mode.

        global_heating = self._run_data_statuscode in _STAT_HVAC_ACTION_HEATING
        global_cooling = self._run_data_statuscode in _STAT_HVAC_ACTION_COOLING

        if self._hc_statuscode is not None:
            # Hc{n}Status=1 means this circuit is contributing; 0 means idle for this zone
            zone_active = self._hc_statuscode not in ("0", "false", "inactive", "off")
            if global_heating and zone_active:
                return HVACAction.HEATING
            if global_cooling and zone_active:
                return HVACAction.COOLING
            if global_heating or global_cooling:
                return HVACAction.IDLE
        else:
            if global_heating:
                return HVACAction.HEATING
            if global_cooling:
                return HVACAction.COOLING

        if self._attr_hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if self._attr_hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if self._attr_hvac_mode == HVACMode.AUTO:
            return HVACAction.IDLE
        return HVACAction.OFF

    @callback
    def _apply_vocab_modes(self) -> None:
        self._attr_hvac_modes = [
            _HA_HVAC_MODE[m]
            for m in ZONE_HVAC_MODES[self._mode_vocab]
            if self._config.cooling or m != "cool"
        ]

    @callback
    def _set_mode_vocab(self, vocab: str) -> None:
        if vocab == self._mode_vocab:
            return
        self._mode_vocab = vocab
        self._apply_vocab_modes()

    @callback
    def _handle_mode(self, value: str) -> None:
        if (vocab := mode_vocab_from_value(value)) is not None:
            self._mode_vocab_observed = True
            self._set_mode_vocab(vocab)
        ha_mode = EBUSD_TO_HA_HVAC.get(str(value), "off")
        hvac_mode = _HA_HVAC_MODE.get(ha_mode, HVACMode.OFF)
        if hvac_mode != self._attr_hvac_mode:
            self._clear_pending_target()
        self._attr_hvac_mode = hvac_mode
        self._attr_hvac_action = self._determine_hvac_action()

    @callback
    def _handle_run_data_statuscode(self, value: Any) -> None:
        self._run_data_statuscode = str(value)
        self._activity_values["statuscode"] = value
        self._attr_hvac_action = self._determine_hvac_action()

    @callback
    def _handle_zone_statuscode(self, value: Any) -> None:
        self._zone_statuscode = str(value)
        self._attr_hvac_action = self._determine_hvac_action()

    @callback
    def _handle_hc_statuscode(self, value: Any) -> None:
        self._hc_statuscode = str(value)
        self._attr_hvac_action = self._determine_hvac_action()

    @callback
    def _handle_current_temp(self, value: Any) -> None:
        try:
            self._attr_current_temperature = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_target_temp(self, value: Any) -> None:
        try:
            self._attr_target_temperature = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_temp_desired(self, value: Any) -> None:
        desired = _float_or_none(value)
        if desired is None:
            return
        self._temp_desired = desired
        if self._pending_target is not None and desired != self._pending_desired:
            self._clear_pending_target()

    @callback
    def _handle_quick_veto_temp(self, value: Any) -> None:
        self._quick_veto_value = _float_or_none(value)

    @callback
    def _clear_pending_target(self) -> None:
        self._pending_target = None
        self._pending_since = None
        self._pending_desired = None

    @property
    def target_temperature(self) -> float | None:
        """The manual setpoint in manual mode; the effective target in time-controlled mode."""
        if not (self._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE):
            return self._attr_target_temperature
        if (
            self._pending_target is not None
            and self._pending_since is not None
            and dt_util.utcnow() - self._pending_since < _PENDING_TARGET_TIMEOUT
        ):
            return self._pending_target
        if self._attr_hvac_mode == HVACMode.AUTO:
            if self._quick_veto_value is not None and self.preset_mode == PRESET_BOOST:
                return self._quick_veto_value
            if self._temp_desired is not None:
                return self._temp_desired
        return self._attr_target_temperature

    @callback
    def _handle_target_high(self, value: Any) -> None:
        try:
            self._attr_target_temperature_high = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_target_low(self, value: Any) -> None:
        try:
            self._attr_target_temperature_low = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_holiday_start(self, value: Any) -> None:
        self._holiday_start = str(value)

    @callback
    def _handle_holiday_end(self, value: Any) -> None:
        self._holiday_end = str(value)

    @callback
    def _handle_quick_veto_end_date(self, value: Any) -> None:
        self._quick_veto_end_date = str(value)

    @callback
    def _handle_quick_veto_end_time(self, value: Any) -> None:
        self._quick_veto_end_time = str(value)

    @property
    def preset_mode(self) -> str | None:
        if not (self._attr_supported_features & ClimateEntityFeature.PRESET_MODE):
            return None
        if self._quick_veto_end_date and self._quick_veto_end_time:
            try:
                veto_end = datetime.strptime(
                    f"{self._quick_veto_end_date} {self._quick_veto_end_time}",
                    f"{_DATE_FMT} {_TIME_FMT}",
                )
                if veto_end > datetime.now():
                    return PRESET_BOOST
            except ValueError:
                pass
        if self._holiday_start and self._holiday_end:
            try:
                now = datetime.now().date()
                start = datetime.strptime(self._holiday_start, _DATE_FMT).date()
                end = datetime.strptime(self._holiday_end, _DATE_FMT).date()
                if start <= now <= end:
                    return PRESET_AWAY
            except ValueError:
                pass
        return PRESET_NONE

    async def _publish(self, topic: str, payload: str) -> None:
        _LOGGER.debug("MQTT publish: %s -> %s", topic, payload)
        await mqtt.async_publish(self.hass, topic, payload)

    async def _cancel_quick_veto(self) -> None:
        if self._config.quick_veto_duration and self._config.quick_veto_duration.write_topic:
            await self._publish(self._config.quick_veto_duration.write_topic, "0")
        if self._config.quick_veto_end_date and self._config.quick_veto_end_date.write_topic:
            await self._publish(
                self._config.quick_veto_end_date.write_topic, _QUICK_VETO_CANCEL_DATE
            )
        if self._config.quick_veto_end_time and self._config.quick_veto_end_time.write_topic:
            await self._publish(
                self._config.quick_veto_end_time.write_topic, _QUICK_VETO_CANCEL_TIME
            )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        current = self.preset_mode
        if current == PRESET_BOOST and preset_mode != PRESET_BOOST:
            await self._cancel_quick_veto()
        if preset_mode == PRESET_BOOST and current != PRESET_BOOST:
            # Boost = start a quick veto at the configured quick-veto temperature for
            # the configured duration, the same write path as the Quick Veto switch.
            await self._publish_quick_veto(self._quick_veto_temp)
        if preset_mode == PRESET_AWAY and current != PRESET_AWAY:
            today = datetime.now().date()
            start_str = today.strftime(_DATE_FMT)
            end_str = (today + timedelta(days=self._away_duration)).strftime(_DATE_FMT)
            if self._config.holiday_start:
                await self._publish(self._config.holiday_start.write_topic, start_str)
            if self._config.holiday_end:
                await self._publish(self._config.holiday_end.write_topic, end_str)
        elif preset_mode != PRESET_AWAY and current == PRESET_AWAY:
            if self._config.holiday_start:
                await self._publish(self._config.holiday_start.write_topic, _HOLIDAY_RESET)
            if self._config.holiday_end:
                await self._publish(self._config.holiday_end.write_topic, _HOLIDAY_RESET)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        ebusd_mode = HA_TO_EBUSD_HVAC[self._mode_vocab].get(hvac_mode.value)
        if ebusd_mode is None:
            _LOGGER.warning(
                "HVAC mode %s is not supported by %s (%s mode vocabulary)",
                hvac_mode,
                self.entity_id,
                self._mode_vocab,
            )
            return
        cfg = self._config.mode
        if cfg.write_topic is None:
            return
        if cfg.write_key:
            payload = json.dumps({cfg.write_key: ebusd_mode})
        else:
            payload = ebusd_mode
        await self._publish(cfg.write_topic, payload)

    async def _write_setpoint(
        self, cfg: TopicConfig, value: float, skip_unchanged: bool = True
    ) -> None:
        """Setpoint writes go through the coordinator's EEPROM write guard."""
        if cfg.write_topic is None:
            return
        if self._coordinator is None:
            await self._publish(cfg.write_topic, str(value))
            return
        await self._coordinator.async_write_setpoint(cfg, str(value), skip_unchanged)

    async def _publish_quick_veto(self, temp: float, hours: float | None = None) -> None:
        qv = self._config.quick_veto_temp
        if qv and qv.write_topic:
            # writing the quick veto starts it, even when the value is unchanged
            await self._write_setpoint(qv, temp, skip_unchanged=False)
        qd = self._config.quick_veto_duration
        if qd and qd.write_topic:
            duration = self._quick_veto_duration if hours is None else hours
            await self._publish(qd.write_topic, f"{duration:g}")

    def _writes_setpoint(self) -> bool:
        """Whether a temperature change writes the permanent manual setpoint."""
        cfg = self._config.manual_temperature
        return (
            self._temperature_write == TEMPERATURE_WRITE_SMART
            and self._attr_hvac_mode == HVACMode.HEAT
            and cfg is not None
            and cfg.write_topic is not None
        )

    async def _set_heating_target(self, temp: float) -> None:
        if self._writes_setpoint():
            await self._write_setpoint(self._config.manual_temperature, temp)
            self._attr_target_temperature = temp
            return
        await self._publish_quick_veto(temp)
        self._pending_target = temp
        self._pending_since = dt_util.utcnow()
        self._pending_desired = self._temp_desired

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None and self._config.target_temperature:
            await self._set_heating_target(float(temp))

        high = kwargs.get("target_temp_high")
        low = kwargs.get("target_temp_low")
        if high is not None and self._config.target_temperature_high:
            await self._write_setpoint(self._config.target_temperature_high, high)
        if low is not None and self._config.target_temperature_low:
            manual = self._config.manual_temperature
            low_cfg = self._config.target_temperature_low
            if self._writes_setpoint() and manual.read_topic == low_cfg.read_topic:
                await self._write_setpoint(manual, low)
            else:
                await self._publish_quick_veto(low)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)


class _EbusdSetpointBase(ClimateEntity):
    """Shared base for simple setpoint-only climate entities (no modes or presets)."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_hvac_action = HVACAction.IDLE

    def __init__(self, hass: HomeAssistant, coordinator: EbusdCoordinator) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._run_data_statuscode: str | None = None
        self._unsubscribe: list[Any] = []

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

    async def _subscribe_run_data_status(self, topic_cfg: TopicConfig | None) -> None:
        if topic_cfg is None:
            return
        await self._subscribe(topic_cfg, self._handle_run_data_statuscode)
        self._seed(topic_cfg, self._handle_run_data_statuscode)

    @callback
    def _handle_run_data_statuscode(self, value: Any) -> None:
        self._run_data_statuscode = str(value)
        self._attr_hvac_action = self._compute_hvac_action()

    def _compute_hvac_action(self) -> HVACAction:
        if self._run_data_statuscode in _STAT_HVAC_ACTION_HEATING:
            return HVACAction.HEATING
        if self._run_data_statuscode in _STAT_HVAC_ACTION_COOLING:
            return HVACAction.COOLING
        return HVACAction.IDLE

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubscribe:
            unsub()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        pass


class EbusdFlowTempRangeEntity(_EbusdSetpointBase):
    """Min/max heating circuit flow temperature range (Hc{n}MinFlowTempDesired / Max)."""

    _attr_hvac_modes = [HVACMode.AUTO]
    _attr_hvac_mode = HVACMode.AUTO
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

    def __init__(
        self, hass: HomeAssistant, config: DiscoveredFlowTempRange, coordinator: EbusdCoordinator
    ) -> None:
        super().__init__(hass, coordinator)
        self._config = config
        self._attr_name = "Heating Flow Temperature"
        self._attr_unique_id = f"ebusd_flow_temp_range_{config.key}"
        self._attr_device_info = build_device_info(config)
        self._attr_min_temp = config.min_temp
        self._attr_max_temp = config.max_temp
        self._attr_target_temperature_step = config.temp_step
        self._attr_target_temperature_low: float | None = None
        self._attr_target_temperature_high: float | None = None
        self._attr_current_temperature: float | None = None

    async def async_added_to_hass(self) -> None:
        await self._subscribe(self._config.min_flow_temp, self._handle_min)
        await self._subscribe(self._config.max_flow_temp, self._handle_max)
        if self._config.current_flow_temp:
            await self._subscribe(self._config.current_flow_temp, self._handle_current_flow_temp)
            self._seed(self._config.current_flow_temp, self._handle_current_flow_temp)
        await self._subscribe_run_data_status(self._config.run_data_status)
        self._seed(self._config.min_flow_temp, self._handle_min)
        self._seed(self._config.max_flow_temp, self._handle_max)
        self.async_write_ha_state()

    @callback
    def _handle_min(self, value: Any) -> None:
        try:
            self._attr_target_temperature_low = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_max(self, value: Any) -> None:
        try:
            self._attr_target_temperature_high = float(value)
        except TypeError, ValueError:
            pass

    @callback
    def _handle_current_flow_temp(self, value: Any) -> None:
        try:
            self._attr_current_temperature = float(value)
        except TypeError, ValueError:
            pass

    async def async_set_temperature(self, **kwargs: Any) -> None:
        low = kwargs.get("target_temp_low")
        high = kwargs.get("target_temp_high")
        if low is not None:
            await self._coordinator.async_write_setpoint(self._config.min_flow_temp, str(low))
        if high is not None:
            await self._coordinator.async_write_setpoint(self._config.max_flow_temp, str(high))
