"""Fixes from the live install on the newer definitions (feedback on 1.13.1)."""

import json

import pytest
from homeassistant.components.climate import PRESET_BOOST, PRESET_NONE
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant import device as device_module
from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import (
    DiscoveredClimate,
    DiscoveredFlowTempRange,
    _analyze,
)

C = "ebusd/ctlv3"
ZONE = "climate.vaillant_zone_1"
BOOST = "switch.vaillant_zone_1_quick_veto"
TARGET = "sensor.vaillant_zone_1_effective_target_temperature"

NEW = {
    f"{C}/Z1ManualTemp": 20.0,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1TempDesired": 0.0,
    f"{C}/Z1QuickVetoTemp": 21.0,
    f"{C}/Z1QuickVetoDuration": 3,
    f"{C}/Z1QuickVetoEndDate": "02.10.2026",
    f"{C}/Z1QuickVetoEndTime": "23:57:00",
    f"{C}/Z1SFMode": "auto",
    f"{C}/Z1Status": "auto",
    "ebusd/hmu/YieldCooling": 0,
    f"{C}/Z1OpMode": "manual",
}
OLD = {
    f"{C}/Z1DayTemp": 20.0,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1QuickVetoTemp": 21.0,
    f"{C}/Z1QuickVetoDuration": 3,
    f"{C}/Z1QuickVetoEndDate": "01.01.2015",
    f"{C}/Z1QuickVetoEndTime": "00:00:00",
    f"{C}/Z1SFMode": "auto",
    "ebusd/hmu/YieldCooling": 0,
    f"{C}/Z1OpMode": "day",
}


def _by_device(msgs: dict) -> dict:
    out: dict = {}
    for topic, value in msgs.items():
        _, dev, name = topic.split("/")
        out.setdefault(dev, {})[name] = {"value": {"value": value}}
    return out


async def _setup(hass, msgs: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
    return entry


async def _send(hass, name: str, value) -> None:
    async_fire_mqtt_message(hass, f"{C}/{name}", json.dumps({"value": {"value": value}}))
    await hass.async_block_till_done()


async def _call(hass, mqtt_client_mock, domain, service, data) -> dict[str, str]:
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(domain, service, data, blocking=True)
    out = {}
    for c in mqtt_client_mock.publish.call_args_list:
        payload = c.args[1]
        out[c.args[0]] = payload.decode() if isinstance(payload, bytes) else str(payload)
    return out


# --- 1. quick veto on the newer definitions --------------------------------------------


def test_new_definitions_use_sf_mode_and_read_only_end():
    z1 = next(e for e in _analyze(_by_device(NEW), "ebusd") if isinstance(e, DiscoveredClimate))
    assert z1.sf_mode.write_topic == f"{C}/Z1SFMode/set"
    assert z1.quick_veto_end_date.write_topic is None
    assert z1.quick_veto_end_time.write_topic is None


def test_old_definitions_keep_writable_end_and_no_sf_mode():
    z1 = next(e for e in _analyze(_by_device(OLD), "ebusd") if isinstance(e, DiscoveredClimate))
    assert z1.sf_mode is None
    assert z1.quick_veto_end_date.write_topic == f"{C}/Z1QuickVetoEndDate/set"


async def test_boost_state_follows_sf_mode(hass, mqtt_mock):
    await _setup(hass, NEW)
    # end date/time still say 23:57 today, but SFMode says no veto
    assert hass.states.get(BOOST).state == "off"
    assert hass.states.get(ZONE).attributes["preset_mode"] == PRESET_NONE
    await _send(hass, "Z1SFMode", "veto")
    assert hass.states.get(BOOST).state == "on"
    assert hass.states.get(ZONE).attributes["preset_mode"] == PRESET_BOOST
    await _send(hass, "Z1SFMode", "auto")  # ended or cancelled on the controller
    assert hass.states.get(BOOST).state == "off"


@pytest.mark.parametrize(
    ("domain", "service", "data"),
    [
        ("switch", "turn_off", {"entity_id": BOOST}),
        ("climate", "set_preset_mode", {"entity_id": ZONE, "preset_mode": PRESET_NONE}),
        (DOMAIN, "cancel_quick_veto", {"entity_id": ZONE}),
    ],
    ids=["switch", "preset", "service"],
)
async def test_cancel_writes_sf_mode_auto(hass, mqtt_mock, mqtt_client_mock, domain, service, data):
    await _setup(hass, NEW)
    await _send(hass, "Z1SFMode", "veto")
    out = await _call(hass, mqtt_client_mock, domain, service, data)
    assert out[f"{C}/Z1SFMode/set"] == "auto"
    assert f"{C}/Z1QuickVetoEndDate/set" not in out
    assert f"{C}/Z1QuickVetoEndTime/set" not in out
    assert f"{C}/Z1QuickVetoDuration/set" not in out


async def test_old_definitions_cancel_unchanged(hass, mqtt_mock, mqtt_client_mock, freezer):
    freezer.move_to("2026-10-02 12:00:00")
    await _setup(hass, OLD)
    await _send(hass, "Z1QuickVetoEndDate", "02.10.2026")
    await _send(hass, "Z1QuickVetoEndTime", "15:00:00")
    assert hass.states.get(BOOST).state == "on"
    out = await _call(hass, mqtt_client_mock, "switch", "turn_off", {"entity_id": BOOST})
    assert out[f"{C}/Z1QuickVetoDuration/set"] == "0"
    assert out[f"{C}/Z1QuickVetoEndDate/set"] == "01.01.2015"
    assert f"{C}/Z1SFMode/set" not in out


# --- 2. flow temperature limits ---------------------------------------------------------


def test_new_flow_limit_names_win_over_retained_old_ones():
    msgs = {
        f"{C}/Hc1HeatingFlowTempMin": 20,
        f"{C}/Hc1HeatingFlowTempMax": 50,
        f"{C}/Hc1MinFlowTempDesired": 15,  # stale retained value of the old name
        f"{C}/Hc1MaxFlowTempDesired": 45,
        f"{C}/Hc1FlowTemp": 31.5,
    }
    flow = next(
        e for e in _analyze(_by_device(msgs), "ebusd") if isinstance(e, DiscoveredFlowTempRange)
    )
    assert flow.min_flow_temp.write_topic == f"{C}/Hc1HeatingFlowTempMin/set"
    assert flow.max_flow_temp.write_topic == f"{C}/Hc1HeatingFlowTempMax/set"


def test_old_flow_limit_names_still_work():
    msgs = {
        f"{C}/Hc1MinFlowTempDesired": 15,
        f"{C}/Hc1MaxFlowTempDesired": 45,
        f"{C}/Hc1FlowTemp": 31.5,
    }
    flow = next(
        e for e in _analyze(_by_device(msgs), "ebusd") if isinstance(e, DiscoveredFlowTempRange)
    )
    assert flow.min_flow_temp.read_topic == f"{C}/Hc1MinFlowTempDesired"


# --- 3. effective target ----------------------------------------------------------------


async def test_effective_target_in_each_mode(hass, mqtt_mock):
    await _setup(hass, NEW)
    assert hass.states.get(TARGET).state == "20.0"  # manual: TempDesired is 0
    await _send(hass, "Z1SFMode", "veto")
    assert hass.states.get(TARGET).state == "21.0"  # quick veto temperature
    await _send(hass, "Z1SFMode", "auto")
    await _send(hass, "Z1OpMode", "auto")
    await _send(hass, "Z1TempDesired", 18.5)
    assert hass.states.get(TARGET).state == "18.5"  # schedule
    await _send(hass, "Z1TempDesired", 0.0)
    assert hass.states.get(TARGET).state == "unknown"


async def test_climate_ignores_zero_temp_desired(hass, mqtt_mock):
    await _setup(hass, {**NEW, f"{C}/Z1OpMode": "auto"})
    assert hass.states.get(ZONE).attributes["temperature"] == 20.0  # not 0


# --- 4. no misleading "off" after a reload --------------------------------------------


async def test_zone_state_from_cache_right_after_reload(hass, mqtt_mock):
    entry = await _setup(hass, NEW)
    assert hass.states.get(ZONE).state == "heat"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # the first message after the reload creates the entity; it must not show "off"
    for topic, value in NEW.items():
        async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
        state = hass.states.get(ZONE)
        assert state is None or state.state != "off", topic


def test_zone_starts_unknown_not_off(hass):
    from custom_components.ebusd_vaillant.climate import EbusdClimateEntity

    z1 = next(e for e in _analyze(_by_device(NEW), "ebusd") if isinstance(e, DiscoveredClimate))
    entity = EbusdClimateEntity(hass, z1)
    assert entity.hvac_mode is None
    assert entity.hvac_action is None


# --- 5. via_device_id -------------------------------------------------------------------


def test_device_info_uses_via_device_id_when_supported(monkeypatch):
    z1 = next(e for e in _analyze(_by_device(NEW), "ebusd") if isinstance(e, DiscoveredClimate))
    monkeypatch.setattr(device_module, "_SUPPORTS_VIA_DEVICE_ID", True)
    monkeypatch.setitem(device_module.PARENT_DEVICE_IDS, "ebusd", "parent-device-id")
    info = device_module.build_device_info(z1)
    assert info["via_device_id"] == "parent-device-id"
    assert "via_device" not in info

    monkeypatch.setattr(device_module, "_SUPPORTS_VIA_DEVICE_ID", False)
    assert device_module.build_device_info(z1)["via_device"] == (DOMAIN, "ebusd")


# --- 1.13.3: Heating boost switch created before Z{n}SFMode arrives -------------------


async def test_boost_switch_works_when_sf_mode_arrives_after_discovery(
    hass, mqtt_mock, mqtt_client_mock
):
    """Startup order on the live system: OpMode + ManualTemp first, SFMode later."""
    first = {k: v for k, v in NEW.items() if not k.endswith("Z1SFMode")}
    await _setup(hass, first)
    assert hass.states.get(BOOST) is not None

    await _send(hass, "Z1SFMode", "veto")
    assert hass.states.get(BOOST).state == "on"
    out = await _call(hass, mqtt_client_mock, "switch", "turn_off", {"entity_id": BOOST})
    assert out[f"{C}/Z1SFMode/set"] == "auto"
    assert f"{C}/Z1QuickVetoDuration/set" not in out
    await _send(hass, "Z1SFMode", "auto")
    assert hass.states.get(BOOST).state == "off"


async def test_boost_switch_picks_up_sf_mode_from_later_discovery(
    hass, mqtt_mock, mqtt_client_mock
):
    """Created while the vocabulary is still ambiguous (no SFMode topic), then updated."""
    # a stale retained Z1DayTemp arrives first, so the vocabulary is still undecided
    # (day) when OpMode=auto creates the zone and its switch
    ambiguous = {f"{C}/Z1DayTemp": 19.0}
    ambiguous.update({k: v for k, v in NEW.items() if not k.endswith("Z1SFMode")})
    ambiguous[f"{C}/Z1OpMode"] = "auto"
    await _setup(hass, ambiguous)
    await _send(hass, "Z1OpMode", "manual")  # now clearly the newer definitions
    await _send(hass, "Z1SFMode", "veto")
    assert hass.states.get(BOOST).state == "on"
    out = await _call(hass, mqtt_client_mock, "switch", "turn_off", {"entity_id": BOOST})
    assert out[f"{C}/Z1SFMode/set"] == "auto"
    assert f"{C}/Z1QuickVetoDuration/set" not in out


def test_sf_mode_topic_exists_before_first_value():
    first = {k: v for k, v in NEW.items() if not k.endswith("Z1SFMode")}
    z1 = next(e for e in _analyze(_by_device(first), "ebusd") if isinstance(e, DiscoveredClimate))
    assert z1.sf_mode.write_topic == f"{C}/Z1SFMode/set"


# --- 1.14: the boost switch turns on at once ------------------------------------------------


async def test_boost_switch_turns_on_optimistically_new_definitions(hass, mqtt_mock):
    await _setup(hass, NEW)
    assert hass.states.get(BOOST).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": BOOST}, blocking=True)
    assert hass.states.get(BOOST).state == "on"  # without waiting for the next SFMode poll
    await _send(hass, "Z1SFMode", "auto")  # the controller did not take it
    assert hass.states.get(BOOST).state == "off"


async def test_boost_switch_turns_on_optimistically_old_definitions(hass, mqtt_mock, freezer):
    freezer.move_to("2026-10-02 12:00:00")
    await _setup(hass, {k: v for k, v in OLD.items() if "SFMode" not in k})
    assert hass.states.get(BOOST).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": BOOST}, blocking=True)
    state = hass.states.get(BOOST)
    assert state.state == "on"
    assert state.attributes["boost_ends_at"].startswith("2026-10-02T15:00")  # 3 hours
