"""Old (day/night) vs. new (manual) operating mode vocabulary of the controller definitions.

Older ebusd-configuration files define Z{n}OpMode/HwcOpMode as 0=off;1=auto;2=day;3=night
and the zone setpoint as Z{n}DayTemp. The TypeSpec-based 15.ctlv2/ctlv3 definitions use
0=off;1=auto;2=manual and Z{n}ManualTemp. Both must keep working.
"""

import json

import pytest
from homeassistant.components.climate import HVACMode
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import (
    DiscoveredClimate,
    DiscoveredWaterHeater,
    _analyze,
    mode_vocab_from_value,
)
from tests.conftest import DATA_DIR, load_data_file

MQTT_PREFIX = "ebusd"
DEVICE = "ctlv3"


def _msgs(zone_mode: str, hwc_mode: str, setpoint: str) -> dict[str, dict]:
    # Setpoint messages before the OpMode messages, so the first discovery sees them.
    return {
        f"{MQTT_PREFIX}/{DEVICE}/{setpoint}": {"value": {"value": 20.5}},
        f"{MQTT_PREFIX}/{DEVICE}/Z1RoomTemp": {"value": {"value": 21.0}},
        f"{MQTT_PREFIX}/{DEVICE}/HwcTempDesired": {"value": {"value": 50}},
        f"{MQTT_PREFIX}/{DEVICE}/HwcStorageTemp": {"value": {"value": 48}},
        f"{MQTT_PREFIX}/{DEVICE}/HwcSFMode": {"value": {"value": "auto"}},
        f"{MQTT_PREFIX}/{DEVICE}/HwcOpMode": {"value": {"value": hwc_mode}},
        f"{MQTT_PREFIX}/{DEVICE}/Z1OpMode": {"value": {"value": zone_mode}},
    }


OLD_DAY = _msgs("day", "day", "Z1DayTemp")
OLD_AUTO = _msgs("auto", "auto", "Z1DayTemp")
NEW_MANUAL = _msgs("manual", "manual", "Z1ManualTemp")
NEW_AUTO = _msgs("auto", "auto", "Z1ManualTemp")


@pytest.fixture
async def setup_entry(hass, mqtt_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": MQTT_PREFIX})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _fire(hass, msgs: dict) -> None:
    """Fire MQTT messages twice: first pass triggers discovery, second updates entity state."""
    for _ in range(2):
        for topic, payload in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps(payload))
        await hass.async_block_till_done()


async def _fire_one(hass, name: str, value) -> None:
    async_fire_mqtt_message(
        hass, f"{MQTT_PREFIX}/{DEVICE}/{name}", json.dumps({"value": {"value": value}})
    )
    await hass.async_block_till_done()


def _climate(hass):
    states = hass.states.async_all("climate")
    assert len(states) == 1
    return states[0]


def _water_heater(hass):
    states = hass.states.async_all("water_heater")
    assert len(states) == 1
    return states[0]


def _published(mqtt_client_mock) -> dict[str, str]:
    result: dict[str, str] = {}
    for c in mqtt_client_mock.publish.call_args_list:
        topic = c.args[0]
        payload = c.args[1] if len(c.args) > 1 else ""
        result[topic] = payload.decode() if isinstance(payload, bytes) else str(payload)
    return result


async def _call(hass, mqtt_client_mock, domain: str, service: str, data: dict) -> dict[str, str]:
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(domain, service, data, blocking=True)
    return _published(mqtt_client_mock)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _by_device(msgs: dict[str, dict]) -> dict[str, dict]:
    return {DEVICE: {topic.rsplit("/", 1)[1]: payload for topic, payload in msgs.items()}}


def _discover(msgs: dict[str, dict]) -> tuple[DiscoveredClimate, DiscoveredWaterHeater]:
    entities = _analyze(_by_device(msgs), MQTT_PREFIX)
    climate = next(e for e in entities if isinstance(e, DiscoveredClimate))
    water_heater = next(e for e in entities if isinstance(e, DiscoveredWaterHeater))
    return climate, water_heater


@pytest.mark.parametrize(
    ("value", "expected"),
    [("manual", "manual"), ("day", "day"), ("night", "day"), ("auto", None), ("off", None)],
)
def test_mode_vocab_from_value(value, expected):
    assert mode_vocab_from_value(value) == expected


@pytest.mark.parametrize("msgs", [OLD_DAY, OLD_AUTO], ids=["day", "auto"])
def test_discovery_old_definitions(msgs):
    climate, water_heater = _discover(msgs)
    assert climate.mode_vocab == "day"
    assert climate.hvac_modes == ["auto", "heat", "cool", "off"]
    assert climate.target_temperature.read_topic == f"{MQTT_PREFIX}/{DEVICE}/Z1DayTemp"
    assert water_heater.mode_vocab == "day"
    assert water_heater.operation_modes == ["auto", "day", "off"]


@pytest.mark.parametrize("msgs", [NEW_MANUAL, NEW_AUTO], ids=["manual", "auto"])
def test_discovery_new_definitions(msgs):
    """Detected from the OpMode value, or from Z1ManualTemp when the mode is auto."""
    climate, water_heater = _discover(msgs)
    assert climate.mode_vocab == "manual"
    assert climate.hvac_modes == ["auto", "heat", "off"]
    assert climate.target_temperature.read_topic == f"{MQTT_PREFIX}/{DEVICE}/Z1ManualTemp"
    assert water_heater.mode_vocab == "manual"
    assert water_heater.operation_modes == ["auto", "manual", "off"]


def test_discovery_stale_day_temp_ignored_with_manual_vocab():
    """A retained Z1DayTemp from the old definitions must not win over Z1ManualTemp."""
    msgs = {**NEW_MANUAL, f"{MQTT_PREFIX}/{DEVICE}/Z1DayTemp": {"value": {"value": 19.0}}}
    climate, _ = _discover(msgs)
    assert climate.mode_vocab == "manual"
    assert climate.target_temperature.read_topic == f"{MQTT_PREFIX}/{DEVICE}/Z1ManualTemp"


def test_discovery_day_and_manual_temp_without_value_defaults_to_day():
    """Ambiguous keys and an ambiguous mode value keep the old behaviour."""
    msgs = {**NEW_AUTO, f"{MQTT_PREFIX}/{DEVICE}/Z1DayTemp": {"value": {"value": 19.0}}}
    climate, water_heater = _discover(msgs)
    assert climate.mode_vocab == "day"
    assert water_heater.mode_vocab == "day"


def test_vrc720_manual_data_file():
    """VRC 720/3 + aroTHERM topics as published with the TypeSpec-based definitions."""
    prefix, by_device = load_data_file(DATA_DIR / "vrc720_manual.yml")
    entities = _analyze(by_device, prefix)
    zones = [e for e in entities if isinstance(e, DiscoveredClimate)]
    water_heaters = [e for e in entities if isinstance(e, DiscoveredWaterHeater)]
    assert [z.key for z in zones] == ["ctlv3_zone1"]
    assert zones[0].mode_vocab == "manual"
    assert zones[0].target_temperature.read_topic == "ebusd/ctlv3/Z1ManualTemp"
    assert len(water_heaters) == 1
    assert water_heaters[0].operation_modes == ["auto", "manual", "off"]
    assert water_heaters[0].sf_mode is not None


# ---------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("msgs", "written"),
    [(OLD_DAY, "day"), (OLD_AUTO, "day"), (NEW_MANUAL, "manual"), (NEW_AUTO, "manual")],
    ids=["old-day", "old-auto", "new-manual", "new-auto"],
)
async def test_set_hvac_mode_heat_uses_vocab(hass, setup_entry, mqtt_client_mock, msgs, written):
    await _fire(hass, msgs)
    entity_id = _climate(hass).entity_id
    published = await _call(
        hass,
        mqtt_client_mock,
        "climate",
        "set_hvac_mode",
        {"entity_id": entity_id, "hvac_mode": HVACMode.HEAT},
    )
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/Z1OpMode/set") == written


async def test_new_definitions_state_and_target(hass, setup_entry):
    await _fire(hass, NEW_MANUAL)
    state = _climate(hass)
    assert state.state == HVACMode.HEAT
    assert state.attributes["temperature"] == 20.5
    assert HVACMode.COOL not in state.attributes["hvac_modes"]


async def test_old_definitions_keep_cool(hass, setup_entry):
    await _fire(hass, OLD_DAY)
    state = _climate(hass)
    assert state.state == HVACMode.HEAT
    assert HVACMode.COOL in state.attributes["hvac_modes"]


async def test_vocab_learned_from_mode_value(hass, setup_entry, mqtt_client_mock):
    """Discovered with ambiguous data (old default), then a 'manual' value is read."""
    msgs = {**NEW_AUTO, f"{MQTT_PREFIX}/{DEVICE}/Z1DayTemp": {"value": {"value": 19.0}}}
    await _fire(hass, msgs)
    assert HVACMode.COOL in _climate(hass).attributes["hvac_modes"]

    await _fire_one(hass, "Z1OpMode", "manual")
    state = _climate(hass)
    assert state.state == HVACMode.HEAT
    assert HVACMode.COOL not in state.attributes["hvac_modes"]
    published = await _call(
        hass,
        mqtt_client_mock,
        "climate",
        "set_hvac_mode",
        {"entity_id": state.entity_id, "hvac_mode": HVACMode.HEAT},
    )
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/Z1OpMode/set") == "manual"


# ---------------------------------------------------------------------------
# Water heater
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("msgs", "modes"),
    [
        (OLD_AUTO, ["auto", "day", "off", "boost"]),
        (NEW_AUTO, ["auto", "manual", "off", "boost"]),
    ],
    ids=["old", "new"],
)
async def test_water_heater_operation_list(hass, setup_entry, msgs, modes):
    await _fire(hass, msgs)
    assert _water_heater(hass).attributes["operation_list"] == modes


async def test_water_heater_manual_state(hass, setup_entry):
    await _fire(hass, NEW_MANUAL)
    assert _water_heater(hass).state == "manual"


@pytest.mark.parametrize(
    ("msgs", "requested", "written"),
    [
        (OLD_AUTO, "day", "day"),
        (NEW_AUTO, "manual", "manual"),
        (NEW_AUTO, "off", "off"),
    ],
    ids=["old-day", "new-manual", "new-off"],
)
async def test_water_heater_set_operation_mode(
    hass, setup_entry, mqtt_client_mock, msgs, requested, written
):
    await _fire(hass, msgs)
    entity_id = _water_heater(hass).entity_id
    published = await _call(
        hass,
        mqtt_client_mock,
        "water_heater",
        "set_operation_mode",
        {"entity_id": entity_id, "operation_mode": requested},
    )
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/HwcOpMode/set") == written


async def test_water_heater_operation_list_learned_from_value(hass, setup_entry):
    msgs = {**NEW_AUTO, f"{MQTT_PREFIX}/{DEVICE}/Z1DayTemp": {"value": {"value": 19.0}}}
    await _fire(hass, msgs)
    assert "day" in _water_heater(hass).attributes["operation_list"]

    await _fire_one(hass, "HwcOpMode", "manual")
    state = _water_heater(hass)
    assert state.state == "manual"
    assert state.attributes["operation_list"] == ["auto", "manual", "off", "boost"]


@pytest.mark.parametrize(("msgs", "word"), [(OLD_AUTO, "day"), (NEW_AUTO, "manual")])
async def test_water_heater_boost(hass, setup_entry, mqtt_client_mock, msgs, word):
    """Boost writes HwcSFMode=load; leaving boost writes auto, then the regular mode."""
    await _fire(hass, msgs)
    entity_id = _water_heater(hass).entity_id
    published = await _call(
        hass,
        mqtt_client_mock,
        "water_heater",
        "set_operation_mode",
        {"entity_id": entity_id, "operation_mode": "boost"},
    )
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/HwcSFMode/set") == "load"
    assert f"{MQTT_PREFIX}/{DEVICE}/HwcOpMode/set" not in published

    await _fire_one(hass, "HwcSFMode", "load")
    assert _water_heater(hass).state == "boost"

    published = await _call(
        hass,
        mqtt_client_mock,
        "water_heater",
        "set_operation_mode",
        {"entity_id": entity_id, "operation_mode": word},
    )
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/HwcSFMode/set") == "auto"
    assert published.get(f"{MQTT_PREFIX}/{DEVICE}/HwcOpMode/set") == word
