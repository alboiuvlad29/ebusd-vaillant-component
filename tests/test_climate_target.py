"""Zone target temperature: cooling detection, smart setpoint writes, effective target."""

import json

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import (
    CONF_COOLING,
    CONF_TEMPERATURE_WRITE,
    COOLING_DISABLED,
    COOLING_ENABLED,
    DOMAIN,
    TEMPERATURE_WRITE_QUICK_VETO,
)
from custom_components.ebusd_vaillant.discovery import DiscoveredClimate, _analyze

PREFIX = "ebusd"
CTL = "ctlv3"
HMU = "hmu"


def _v(value) -> dict:
    return {"value": {"value": value}}


def _zone(setpoint: str = "Z1DayTemp", mode: str = "auto", **extra) -> dict[str, dict]:
    """Zone 1 messages; setpoints first so the first discovery already sees them."""
    msgs = {
        f"{PREFIX}/{CTL}/{setpoint}": _v(20.5),
        f"{PREFIX}/{CTL}/Z1CoolingTemp": _v(24),
        f"{PREFIX}/{CTL}/Z1RoomTemp": _v(21.0),
        f"{PREFIX}/{CTL}/Z1QuickVetoTemp": _v(21),
        f"{PREFIX}/{CTL}/Z1QuickVetoDuration": _v(3),
    }
    msgs.update({f"{PREFIX}/{k}": _v(v) for k, v in extra.items()})
    msgs[f"{PREFIX}/{CTL}/Z1OpMode"] = _v(mode)
    return msgs


def _by_device(msgs: dict[str, dict]) -> dict[str, dict]:
    by_device: dict[str, dict] = {}
    for topic, payload in msgs.items():
        _, device, name = topic.split("/")
        by_device.setdefault(device, {})[name] = payload
    return by_device


def _discover(msgs: dict[str, dict], **kwargs) -> DiscoveredClimate:
    entities = _analyze(_by_device(msgs), PREFIX, **kwargs)
    return next(e for e in entities if isinstance(e, DiscoveredClimate))


# ---------------------------------------------------------------------------
# Cooling detection
# ---------------------------------------------------------------------------


def test_unknown_cooling_keeps_range():
    """No cooling signal at all: behaviour before 1.1.0 (heat/cool range, cool mode)."""
    z1 = _discover(_zone())
    assert z1.cooling is True
    assert z1.target_temperature is None
    assert z1.target_temperature_high.read_topic == f"{PREFIX}/{CTL}/Z1CoolingTemp"
    assert "cool" in z1.hvac_modes


@pytest.mark.parametrize(
    "extra",
    [
        {f"{HMU}/YieldCooling": 0},
        {f"{CTL}/Hc1CoolingEnabled": "no", f"{HMU}/YieldCooling": 12.5},
        {f"{HMU}/ActiveCoolingEnabled": 0},
    ],
    ids=["zero-yield", "hc-disabled-wins", "active-cooling-off"],
)
def test_cooling_disabled_gives_single_target(extra):
    z1 = _discover(_zone(**extra))
    assert z1.cooling is False
    assert z1.target_temperature.read_topic == f"{PREFIX}/{CTL}/Z1DayTemp"
    assert z1.target_temperature_high is None
    assert z1.target_temperature_low is None
    assert z1.hvac_modes == ["auto", "heat", "off"]


@pytest.mark.parametrize(
    "extra",
    [
        {f"{HMU}/YieldCooling": 60.5},
        {f"{CTL}/Hc1CoolingEnabled": "yes", f"{HMU}/YieldCooling": 0},
        {f"{HMU}/RunDataStatuscode": "cool_compressor_active"},
    ],
    ids=["yield", "hc-enabled-wins", "cool-status"],
)
def test_cooling_enabled_keeps_range(extra):
    z1 = _discover(_zone(**extra))
    assert z1.cooling is True
    assert z1.target_temperature_low.read_topic == f"{PREFIX}/{CTL}/Z1DayTemp"


def test_releasecooling_flag_in_setmode():
    by_device = _by_device(_zone(**{f"{HMU}/YieldCooling": 0}))
    by_device[HMU]["SetMode"] = {"hcmode": {"value": "auto"}, "releasecooling": {"value": 1}}
    entities = _analyze(by_device, PREFIX)
    z1 = next(e for e in entities if isinstance(e, DiscoveredClimate))
    assert z1.cooling is True


@pytest.mark.parametrize(
    ("mode", "extra", "cooling"),
    [
        (COOLING_DISABLED, {f"{HMU}/YieldCooling": 60.5}, False),
        (COOLING_ENABLED, {f"{HMU}/YieldCooling": 0}, True),
    ],
)
def test_cooling_option_overrides_detection(mode, extra, cooling):
    z1 = _discover(_zone(**extra), cooling_mode=mode)
    assert z1.cooling is cooling


def test_day_night_range_collapses_without_cooling():
    msgs = _zone(**{f"{CTL}/Z1NightTemp": 18, f"{HMU}/YieldCooling": 0})
    del msgs[f"{PREFIX}/{CTL}/Z1CoolingTemp"]
    z1 = _discover(msgs)
    assert z1.target_temperature.read_topic == f"{PREFIX}/{CTL}/Z1DayTemp"
    assert z1.target_temperature_high is None


def test_manual_and_desired_topics_discovered():
    z1 = _discover(_zone("Z1ManualTemp", "manual", **{f"{CTL}/Z1TempDesired": 19.5}))
    assert z1.manual_temperature.write_topic == f"{PREFIX}/{CTL}/Z1ManualTemp/set"
    assert z1.temp_desired.read_topic == f"{PREFIX}/{CTL}/Z1TempDesired"
    assert z1.temp_desired.write_topic is None


# ---------------------------------------------------------------------------
# Entity behaviour
# ---------------------------------------------------------------------------


async def _setup(hass, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": PREFIX}, options=options or {})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fire(hass, msgs: dict) -> None:
    for _ in range(2):
        for topic, payload in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps(payload))
        await hass.async_block_till_done()


async def _fire_one(hass, topic: str, value) -> None:
    async_fire_mqtt_message(hass, f"{PREFIX}/{topic}", json.dumps(_v(value)))
    await hass.async_block_till_done()


def _climate(hass):
    states = hass.states.async_all("climate")
    assert len(states) == 1
    return states[0]


def _published(mqtt_client_mock) -> dict[str, str]:
    result: dict[str, str] = {}
    for c in mqtt_client_mock.publish.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else ""
        result[c.args[0]] = payload.decode() if isinstance(payload, bytes) else str(payload)
    return result


async def _set_temperature(hass, mqtt_client_mock, temp: float) -> dict[str, str]:
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": _climate(hass).entity_id, "temperature": temp},
        blocking=True,
    )
    return _published(mqtt_client_mock)


NO_COOLING = {f"{HMU}/YieldCooling": 0}


async def test_single_target_without_cooling(hass, mqtt_mock):
    await _setup(hass)
    await _fire(hass, _zone("Z1DayTemp", "day", **NO_COOLING))
    state = _climate(hass)
    assert state.attributes["temperature"] == 20.5
    assert "target_temp_high" not in state.attributes or state.attributes["target_temp_high"] is None
    assert HVACMode.COOL not in state.attributes["hvac_modes"]


@pytest.mark.parametrize(
    ("setpoint", "mode"), [("Z1DayTemp", "day"), ("Z1ManualTemp", "manual")], ids=["old", "new"]
)
async def test_manual_mode_writes_setpoint(hass, mqtt_mock, mqtt_client_mock, setpoint, mode):
    await _setup(hass)
    await _fire(hass, _zone(setpoint, mode, **NO_COOLING))
    published = await _set_temperature(hass, mqtt_client_mock, 22.0)
    assert published.get(f"{PREFIX}/{CTL}/{setpoint}/set") == "22.0"
    assert f"{PREFIX}/{CTL}/Z1QuickVetoTemp/set" not in published
    assert _climate(hass).attributes["temperature"] == 22.0


async def test_auto_mode_uses_quick_veto_and_shows_it(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    await _fire(hass, _zone("Z1ManualTemp", "auto", **NO_COOLING, **{f"{CTL}/Z1TempDesired": 19}))
    assert _climate(hass).attributes["temperature"] == 19.0  # effective target
    published = await _set_temperature(hass, mqtt_client_mock, 22.5)
    assert published.get(f"{PREFIX}/{CTL}/Z1QuickVetoTemp/set") == "22.5"
    assert f"{PREFIX}/{CTL}/Z1ManualTemp/set" not in published
    assert _climate(hass).attributes["temperature"] == 22.5  # optimistic until confirmed

    await _fire_one(hass, f"{CTL}/Z1TempDesired", 22.5)
    assert _climate(hass).attributes["temperature"] == 22.5


async def test_pending_target_yields_to_new_desired(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    await _fire(hass, _zone("Z1ManualTemp", "auto", **NO_COOLING, **{f"{CTL}/Z1TempDesired": 19}))
    await _set_temperature(hass, mqtt_client_mock, 22.5)
    await _fire_one(hass, f"{CTL}/Z1TempDesired", 18)  # e.g. veto rejected, schedule moved on
    assert _climate(hass).attributes["temperature"] == 18.0


async def test_pending_target_expires(hass, mqtt_mock, mqtt_client_mock, freezer):
    await _setup(hass)
    await _fire(hass, _zone("Z1ManualTemp", "auto", **NO_COOLING, **{f"{CTL}/Z1TempDesired": 19}))
    await _set_temperature(hass, mqtt_client_mock, 22.5)
    freezer.tick(11 * 60)
    async_fire_mqtt_message(hass, f"{PREFIX}/{CTL}/Z1RoomTemp", json.dumps(_v(21.1)))
    await hass.async_block_till_done()
    assert _climate(hass).attributes["temperature"] == 19.0


async def test_manual_mode_shows_setpoint_not_desired(hass, mqtt_mock):
    await _setup(hass)
    await _fire(
        hass, _zone("Z1ManualTemp", "manual", **NO_COOLING, **{f"{CTL}/Z1TempDesired": 19})
    )
    assert _climate(hass).attributes["temperature"] == 20.5


async def test_quick_veto_option_keeps_old_behaviour(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass, {CONF_TEMPERATURE_WRITE: TEMPERATURE_WRITE_QUICK_VETO})
    await _fire(hass, _zone("Z1DayTemp", "day", **NO_COOLING))
    published = await _set_temperature(hass, mqtt_client_mock, 22.0)
    assert published.get(f"{PREFIX}/{CTL}/Z1QuickVetoTemp/set") == "22.0"
    assert f"{PREFIX}/{CTL}/Z1DayTemp/set" not in published


async def test_range_low_in_manual_mode_writes_setpoint(hass, mqtt_mock, mqtt_client_mock):
    """Cooling systems keep the range; in manual mode the low end is the manual setpoint."""
    await _setup(hass)
    await _fire(hass, _zone("Z1DayTemp", "day", **{f"{HMU}/YieldCooling": 60.5}))
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": _climate(hass).entity_id, "target_temp_low": 19.5, "target_temp_high": 25},
        blocking=True,
    )
    published = _published(mqtt_client_mock)
    assert published.get(f"{PREFIX}/{CTL}/Z1DayTemp/set") == "19.5"
    assert published.get(f"{PREFIX}/{CTL}/Z1CoolingTemp/set") == "25.0"
    assert f"{PREFIX}/{CTL}/Z1QuickVetoTemp/set" not in published


async def test_layout_follows_late_cooling_signal(hass, mqtt_mock):
    """Created as a range (nothing known yet); YieldCooling=0 turns it into one target."""
    await _setup(hass)
    await _fire(hass, _zone("Z1DayTemp", "day"))
    assert _climate(hass).attributes.get("target_temp_high") == 24.0

    await _fire_one(hass, f"{HMU}/YieldCooling", 0)
    state = _climate(hass)
    assert state.attributes["temperature"] == 20.5
    assert state.attributes.get("target_temp_high") is None
    assert HVACMode.COOL not in state.attributes["hvac_modes"]

    await _fire_one(hass, f"{CTL}/Z1CoolingTemp", 26)  # no longer bound
    assert _climate(hass).attributes.get("target_temp_high") is None


async def test_rebinds_from_day_temp_to_manual_temp(hass, mqtt_mock):
    """Switching to the newer definitions moves the target from Z1DayTemp to Z1ManualTemp."""
    await _setup(hass)
    await _fire(hass, _zone("Z1DayTemp", "auto", **NO_COOLING))
    assert _climate(hass).attributes["temperature"] == 20.5

    await _fire_one(hass, f"{CTL}/Z1ManualTemp", 21.5)
    await _fire_one(hass, f"{CTL}/Z1OpMode", "manual")
    assert _climate(hass).attributes["temperature"] == 21.5

    await _fire_one(hass, f"{CTL}/Z1DayTemp", 17)  # stale old topic is ignored now
    assert _climate(hass).attributes["temperature"] == 21.5


async def test_options_change_keeps_registry_entries(hass, mqtt_mock):
    """Saving options reloads without removing entities from the registry."""
    entry = await _setup(hass)
    await _fire(hass, _zone("Z1DayTemp", "day", **NO_COOLING))
    registry = er.async_get(hass)
    entity_id = _climate(hass).entity_id
    registry.async_update_entity(entity_id, new_entity_id="climate.ground_floor")
    await hass.async_block_till_done()

    removed: list[str] = []
    hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        lambda event: removed.append(event.data["entity_id"])
        if event.data["action"] == "remove"
        else None,
    )
    hass.config_entries.async_update_entry(entry, options={CONF_COOLING: COOLING_DISABLED})
    await hass.async_block_till_done()

    assert removed == []
    assert registry.async_get("climate.ground_floor") is not None


async def test_options_flow_offers_new_options(hass, mqtt_mock):
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert {CONF_COOLING, CONF_TEMPERATURE_WRITE} <= schema_keys
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_COOLING: COOLING_DISABLED, CONF_TEMPERATURE_WRITE: TEMPERATURE_WRITE_QUICK_VETO},
    )
    assert entry.options[CONF_COOLING] == COOLING_DISABLED
    assert entry.options[CONF_TEMPERATURE_WRITE] == TEMPERATURE_WRITE_QUICK_VETO
