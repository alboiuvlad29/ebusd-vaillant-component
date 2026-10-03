"""v1.14: hot water preset, fault history, Green iQ, outside temperature, noise reduction."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.ebusd_vaillant.const import CONF_ALLOW_INSTALLER, DOMAIN, FAULT_EVENT
from custom_components.ebusd_vaillant.discovery import (
    DiscoveredControl,
    DiscoveredFaultHistory,
    DiscoveredNoiseSchedule,
    DiscoveredOutdoorTemp,
    DiscoveredSensor,
    DiscoveredWaterHeater,
    _analyze,
)
from custom_components.ebusd_vaillant.faults import (
    UNKNOWN_FAULT,
    fault_meaning,
    format_code,
    parse_fault_entry,
)
from custom_components.ebusd_vaillant.noise import NoiseSchedule

C = "ebusd/ctlv3"
H = "ebusd/hmu"


def _v(value):
    return {"value": {"value": value}}


HWC = {
    "HwcOpMode": _v("auto"),
    "HwcTempDesired": _v(50),
    "HwcStorageTemp": _v(48),
}


def _entry(kind, entities):
    return [e for e in entities if isinstance(e, kind)]


# --- discovery, with and without the new topics ---------------------------------------


def test_upstream_definitions_get_no_new_entities():
    by_device = {"ctlv3": dict(HWC), "hmu": {"Currenterror": {"error": {"value": None}}}}
    entities = _analyze(by_device, "ebusd")
    wh = _entry(DiscoveredWaterHeater, entities)[0]
    assert wh.preset is None and wh.eco_temperature is None
    for kind in (
        DiscoveredControl,
        DiscoveredFaultHistory,
        DiscoveredOutdoorTemp,
        DiscoveredNoiseSchedule,
    ):
        assert _entry(kind, entities) == []


def test_preset_and_eco_controls_discovered():
    msgs = {**HWC, "HwcPreset": _v("eco"), "HwcEcoTempDesired": _v(40), "HwcEcoChargeHyst": _v(10)}
    entities = _analyze({"ctlv3": msgs}, "ebusd")
    wh = _entry(DiscoveredWaterHeater, entities)[0]
    assert wh.preset.write_topic == f"{C}/HwcPreset/set"
    assert wh.eco_temperature.read_topic == f"{C}/HwcEcoTempDesired"
    controls = {c.translation_key: c for c in _entry(DiscoveredControl, entities)}
    assert controls["hot_water_preset"].kind == "select"
    assert controls["hot_water_preset"].options == ("comfort", "eco")
    assert controls["hot_water_eco_temperature"].installer is False
    assert controls["eco_charge_hysteresis"].installer is True


def test_green_iq_and_installer_values():
    msgs = {**HWC, "GreenIQ": _v("on"), "CylinderChargeHyst": _v(10), "HwcLockTime": _v(30)}
    entities = _analyze({"ctlv3": msgs}, "ebusd")
    controls = {c.translation_key: c for c in _entry(DiscoveredControl, entities)}
    assert controls["green_iq"].kind == "switch"
    assert controls["green_iq"].device_key == "ebusd"  # the system device
    assert controls["cylinder_charge_hysteresis"].installer
    assert controls["hot_water_lock_time"].topic.write_topic == f"{C}/HwcLockTime/set"


def test_heat_pump_live_values_only_when_present():
    none = _analyze({"hmu": {"Status07": {"power": {"value": 3}}}}, "ebusd")
    assert not [e for e in _entry(DiscoveredSensor, none) if "RunData" in e.topic.read_topic]
    msgs = {
        "RunDataCompressorSpeed": _v(41.5),
        "RunDataHighPressure": _v(18.2),
        "CurrentCompressorUtil": _v(55),
        "NoiseReductionLevel": _v(40),
    }
    sensors = {e.name: e for e in _entry(DiscoveredSensor, _analyze({"hmu": msgs}, "ebusd"))}
    assert sensors["Compressor speed"].unit == "rps"
    assert sensors["High pressure"].device_class == "pressure"
    assert sensors["Noise reduction level"].entity_category == "diagnostic"


def test_outdoor_temperature_sources():
    only_ctl = _analyze({"ctlv3": {**HWC, "OutsideTemp": _v(12.5)}}, "ebusd")
    outdoor = _entry(DiscoveredOutdoorTemp, only_ctl)[0]
    assert outdoor.broadcast is None and outdoor.controller.read_topic == f"{C}/OutsideTemp"
    both = _analyze(
        {"ctlv3": {**HWC, "OutsideTemp": _v(12.5)}, "broadcast": {"outsidetemp": _v(11.0)}},
        "ebusd",
    )
    outdoor = _entry(DiscoveredOutdoorTemp, both)[0]
    assert outdoor.broadcast.read_topic == "ebusd/broadcast/outsidetemp"
    # the broadcast pseudo device creates nothing on its own
    assert not [e for e in both if getattr(e, "device_id", "") == "broadcast"]


def test_fault_history_needs_last_error():
    assert _entry(DiscoveredFaultHistory, _analyze({"hmu": {"Currenterror": {}}}, "ebusd")) == []
    entities = _analyze({"hmu": {"LastError": ENTRY_0}}, "ebusd")
    fault = _entry(DiscoveredFaultHistory, entities)[0]
    assert fault.last_error.read_topic == f"{H}/LastError"
    assert [t.read_topic for t in fault.history][:2] == [f"{H}/FaultHistory0", f"{H}/FaultHistory1"]
    assert len(fault.history) == 10


# --- fault decoding ----------------------------------------------------------------------


def _fault(code, date, time, status=2):
    return {
        "status": {"value": status},
        "time": {"value": time},
        "date": {"value": date},
        "error": {"value": code},
    }


ENTRY_0 = _fault(22, "18.09.2026", "18:41")
REAL_HISTORY = [
    ("18.09.2026", "18:41"),
    ("31.08.2026", "10:44"),
    ("28.06.2026", "06:08"),
    ("01.10.2025", "23:47"),
    ("04.06.2025", "09:13"),
    ("04.06.2025", "08:53"),
    ("04.06.2025", "07:24"),
]


def test_parse_real_history_and_empty_slots():
    entries = [parse_fault_entry(_fault(22, d, t)) for d, t in REAL_HISTORY]
    assert all(e is not None and e.label == "F.022" for e in entries)
    assert entries[0].timestamp.isoformat().startswith("2026-09-18T18:41")
    assert entries[0].meaning == "Building circuit: water pressure too low"
    empty = _fault(0, "00.00.00", "00:00", status=1)
    assert parse_fault_entry(empty) is None
    assert parse_fault_entry({}) is None  # decode error: no fields
    assert parse_fault_entry("ERR: argument value out of valid range") is None
    assert parse_fault_entry(_fault(22, "31.02.2026", "10:00")) is None  # impossible date


def test_fault_code_table():
    assert format_code(22) == "F.022"
    assert format_code(1117) == "F.1117"
    assert fault_meaning(99999) == UNKNOWN_FAULT


# --- noise reduction schedule --------------------------------------------------------------


def _slot(start, end, index=0):
    return {"slotindex": {"value": index}, "htm": {"value": start}, "htm_1": {"value": end}}


def test_noise_schedule_periods_and_edges():
    schedule = NoiseSchedule()
    assert schedule.active_at(datetime(2026, 10, 5, 9, 0)) is None  # nothing known
    for index, (start, end) in enumerate(
        [("00:00", "08:00"), ("14:00", "16:00"), ("18:30", "24:00")]
    ):
        schedule.update("SilentTimer_Monday", _slot(start, end, index))
    monday = datetime(2026, 10, 5)  # a Monday
    assert schedule.active_at(monday.replace(hour=0, minute=0)) is True
    assert schedule.active_at(monday.replace(hour=7, minute=59)) is True
    assert schedule.active_at(monday.replace(hour=8, minute=0)) is False
    assert schedule.active_at(monday.replace(hour=15, minute=30)) is True
    assert schedule.active_at(monday.replace(hour=18, minute=29)) is False
    assert schedule.active_at(monday.replace(hour=23, minute=59)) is True
    # a day without periods is quiet, not unknown
    assert schedule.active_at(datetime(2026, 10, 6, 3, 0)) is False
    schedule.update("SilentTimer_Monday", _slot("00:00", "00:00", 0))  # slot emptied
    assert schedule.active_at(monday.replace(hour=3)) is False


def test_noise_schedule_slot_in_name():
    schedule = NoiseSchedule()
    schedule.update(
        "SilentTimer_Tuesday1", {"htm": {"value": "06:00"}, "htm_1": {"value": "07:00"}}
    )
    assert schedule.active_at(datetime(2026, 10, 6, 6, 30)) is True


# --- entities ----------------------------------------------------------------------------


async def _setup(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options=options or {})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _send(hass, msgs, times=2):
    for _ in range(times):
        for topic, payload in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps(payload))
        await hass.async_block_till_done()


def _published(mqtt_client_mock):
    result = {}
    for call in mqtt_client_mock.publish.call_args_list:
        payload = call.args[1] if len(call.args) > 1 else ""
        result[call.args[0]] = payload.decode() if isinstance(payload, bytes) else str(payload)
    return result


PRESET_MSGS = {
    **{f"{C}/{k}": v for k, v in HWC.items()},
    f"{C}/HwcPreset": _v("eco"),
    f"{C}/HwcEcoTempDesired": _v(40),
}
WH = "water_heater.vaillant_hot_water"


async def test_water_heater_target_follows_preset(hass, mqtt_mock):
    await _setup(hass)
    await _send(hass, PRESET_MSGS)
    state = hass.states.get(WH)
    assert state.attributes["temperature"] == 40  # eco: what the controller heats to
    assert state.attributes["comfort_temperature"] == 50
    assert state.attributes["eco_temperature"] == 40
    assert hass.states.get("select.vaillant_hot_water_hot_water_preset").state == "eco"
    await _send(hass, {f"{C}/HwcPreset": _v("comfort")})
    assert hass.states.get(WH).attributes["temperature"] == 50
    # numbers as published by older ebusd versions
    await _send(hass, {f"{C}/HwcPreset": _v(1)})
    assert hass.states.get(WH).attributes["temperature"] == 40


async def test_set_temperature_writes_to_the_active_setpoint(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    await _send(hass, PRESET_MSGS)
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": WH, "temperature": 42}, blocking=True
    )
    assert _published(mqtt_client_mock)[f"{C}/HwcEcoTempDesired/set"] == "42.0"
    assert f"{C}/HwcTempDesired/set" not in _published(mqtt_client_mock)
    await _send(hass, {f"{C}/HwcPreset": _v("comfort")})
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": WH, "temperature": 55}, blocking=True
    )
    assert _published(mqtt_client_mock)[f"{C}/HwcTempDesired/set"] == "55.0"


async def test_preset_select_writes_and_eco_number(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    await _send(hass, PRESET_MSGS)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.vaillant_hot_water_hot_water_preset", "option": "comfort"},
        blocking=True,
    )
    assert _published(mqtt_client_mock)[f"{C}/HwcPreset/set"] == "comfort"
    number = hass.states.get("number.vaillant_hot_water_hot_water_eco_temperature")
    assert float(number.state) == 40


async def test_without_preset_the_water_heater_is_unchanged(hass, mqtt_mock):
    await _setup(hass)
    await _send(hass, {f"{C}/{k}": v for k, v in HWC.items()})
    state = hass.states.get(WH)
    assert state.attributes["temperature"] == 50
    assert "eco_temperature" not in state.attributes
    assert not hass.states.async_all("select")


async def test_installer_values_read_only_until_allowed(hass, mqtt_mock, mqtt_client_mock):
    msgs = {**{f"{C}/{k}": v for k, v in HWC.items()}, f"{C}/CylinderChargeHyst": _v(10)}
    await _setup(hass)
    await _send(hass, msgs)
    assert hass.states.get("sensor.vaillant_hot_water_cylinder_charging_hysteresis").state == "10.0"
    assert not hass.states.async_all("number")


async def test_installer_values_writable_with_option(hass, mqtt_mock, mqtt_client_mock):
    msgs = {**{f"{C}/{k}": v for k, v in HWC.items()}, f"{C}/CylinderChargeHyst": _v(10)}
    await _setup(hass, {CONF_ALLOW_INSTALLER: True})
    await _send(hass, msgs)
    entity_id = "number.vaillant_hot_water_cylinder_charging_hysteresis"
    assert hass.states.get(entity_id).state == "10.0"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 8}, blocking=True
    )
    assert _published(mqtt_client_mock)[f"{C}/CylinderChargeHyst/set"] == "8.0"


async def test_green_iq_switch(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    await _send(hass, {**{f"{C}/{k}": v for k, v in HWC.items()}, f"{C}/GreenIQ": _v("off")})
    entity_id = "switch.vaillant_green_iq"
    assert hass.states.get(entity_id).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert _published(mqtt_client_mock)[f"{C}/GreenIQ/set"] == "on"
    assert hass.states.get(entity_id).state == "on"


async def test_outside_temperature_prefers_broadcast_then_controller(hass, mqtt_mock):
    await _setup(hass)
    base = {f"{C}/{k}": v for k, v in HWC.items()}
    await _send(hass, {**base, f"{C}/OutsideTemp": _v(12.5)})
    entity_id = "sensor.vaillant_outside_temperature"
    assert float(hass.states.get(entity_id).state) == 12.5  # controller fallback
    await _send(hass, {"ebusd/broadcast/outsidetemp": {"temp2": {"value": 11.0}}})
    assert float(hass.states.get(entity_id).state) == 11.0  # broadcast wins
    await _send(hass, {f"{C}/OutsideTemp": _v(13.0)})
    assert float(hass.states.get(entity_id).state) == 11.0
    # a stale broadcast gives way to the controller value
    later = datetime.now().astimezone() + timedelta(minutes=30)
    with patch("custom_components.ebusd_vaillant.sensor.dt_util.utcnow", return_value=later):
        await _send(hass, {f"{C}/OutsideTemp": _v(14.0)})
    assert float(hass.states.get(entity_id).state) == 14.0


async def test_climate_gets_the_outdoor_temperature(hass, mqtt_mock):
    await _setup(hass)
    zone = {
        f"{C}/Z1OpMode": _v("auto"),
        f"{C}/Z1RoomTemp": _v(21.0),
        f"{C}/Z1ManualTemp": _v(20),
        f"{C}/OutsideTemp": _v(9.5),
    }
    await _send(hass, zone)
    await _send(hass, {f"{C}/Z1RoomTemp": _v(21.5)})
    climate = hass.states.async_all("climate")[0]
    assert climate.attributes["outdoor_temperature"] == 9.5


async def _setup_faults(hass, mqtt_client_mock):
    await _setup(hass)
    await _send(hass, {f"{H}/LastError": ENTRY_0})
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def fast_requests():
    with patch("custom_components.ebusd_vaillant.sensor.FAULT_REQUEST_INTERVAL", 0):
        yield


async def test_fault_history_requested_and_listed(hass, mqtt_mock, mqtt_client_mock, fast_requests):
    await _setup_faults(hass, mqtt_client_mock)
    await hass.async_block_till_done()
    requested = [c.args[0] for c in mqtt_client_mock.publish.call_args_list]
    assert [t for t in requested if "FaultHistory" in t] == [
        f"{H}/FaultHistory{i}/get" for i in range(10)
    ]
    for index, (date, time) in enumerate(REAL_HISTORY):
        async_fire_mqtt_message(
            hass, f"{H}/FaultHistory{index}", json.dumps(_fault(22, date, time))
        )
    for index in (7, 8):  # empty slots
        async_fire_mqtt_message(
            hass, f"{H}/FaultHistory{index}", json.dumps(_fault(0, "00.00.00", "00:00", 1))
        )
    async_fire_mqtt_message(hass, f"{H}/FaultHistory9", "{}")  # slot that failed to decode
    await hass.async_block_till_done()
    state = hass.states.get("sensor.vaillant_heat_pump_last_fault")
    assert state.state == "F.022"
    assert len(state.attributes["history"]) == 7
    assert state.attributes["history"][0]["code"] == "F.022"
    assert state.attributes["meaning"] == "Building circuit: water pressure too low"


STORE_KEY = f"{DOMAIN}.fault_hmu_fault_history"


async def test_new_fault_fires_once_and_survives_restart(
    hass, hass_storage, mqtt_mock, mqtt_client_mock, fast_requests
):
    events = []
    hass.bus.async_listen(FAULT_EVENT, lambda e: events.append(e.data))
    entry = await _setup(hass)
    await _send(hass, {f"{H}/LastError": ENTRY_0})
    assert events == []  # first run takes over the existing history silently
    newer = _fault(731, "02.10.2026", "07:15")
    await _send(hass, {f"{H}/LastError": newer}, times=1)
    assert len(events) == 1
    assert events[0]["code"] == "F.731" and events[0]["meaning"] == "High-pressure switch open"
    issues = ir.async_get(hass).issues
    assert any(key[1].startswith("heat_pump_fault_hmu_fault_history") for key in issues)
    await _send(hass, {f"{H}/LastError": newer}, times=1)
    assert len(events) == 1  # unchanged
    await hass.async_block_till_done()
    async_fire_time_changed(hass, datetime.now().astimezone() + timedelta(seconds=5))
    await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["last_seen"].startswith("2026-10-02T07:15")
    # restart with the same retained LastError: the stored timestamp prevents a repeat
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _send(hass, {f"{H}/LastError": newer})
    assert len(events) == 1


async def test_fault_while_ha_was_down_is_announced(
    hass, hass_storage, mqtt_mock, mqtt_client_mock, fast_requests
):
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"last_seen": "2026-09-18T18:41:00+00:00"},
    }
    events = []
    hass.bus.async_listen(FAULT_EVENT, lambda e: events.append(e.data))
    await _setup(hass)
    await _send(hass, {f"{H}/LastError": _fault(731, "02.10.2026", "07:15")})
    assert len(events) == 1


async def test_stored_timestamp_equal_means_no_event(
    hass, hass_storage, mqtt_mock, mqtt_client_mock, fast_requests
):
    stamp = parse_fault_entry(ENTRY_0).timestamp.isoformat()
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"last_seen": stamp},
    }
    events = []
    hass.bus.async_listen(FAULT_EVENT, lambda e: events.append(e.data))
    await _setup(hass)
    await _send(hass, {f"{H}/LastError": ENTRY_0})
    assert events == []


async def test_retained_history_in_any_order_never_announces(
    hass, hass_storage, mqtt_mock, mqtt_client_mock, fast_requests
):
    events = []
    hass.bus.async_listen(FAULT_EVENT, lambda e: events.append(e.data))
    await _setup(hass)
    await _send(hass, {f"{H}/LastError": ENTRY_0})
    # an older slot arriving first or last changes nothing: only LastError announces
    for index in (4, 2, 0, 1):
        date, time = REAL_HISTORY[index]
        async_fire_mqtt_message(
            hass, f"{H}/FaultHistory{index}", json.dumps(_fault(22, date, time))
        )
    await hass.async_block_till_done()
    assert events == []


async def test_noise_reduction_binary_sensor(hass, mqtt_mock):
    await _setup(hass)
    base = {f"{C}/{k}": v for k, v in HWC.items()}
    monday_noon = datetime(2026, 10, 5, 15, 0).astimezone()
    with patch(
        "custom_components.ebusd_vaillant.binary_sensor.dt_util.now", return_value=monday_noon
    ):
        await _send(hass, {**base, f"{C}/SilentTimer_Monday": _slot("14:00", "16:00")})
        state = hass.states.get("binary_sensor.vaillant_noise_reduction_active")
        assert state.state == "on"
        assert state.attributes["schedule"] == {"Monday": ["14:00-16:00"]}
    quiet = datetime(2026, 10, 5, 12, 0).astimezone()
    with patch("custom_components.ebusd_vaillant.binary_sensor.dt_util.now", return_value=quiet):
        await _send(hass, {f"{C}/SilentTimer_Monday": _slot("14:00", "16:00")}, times=1)
        assert hass.states.get("binary_sensor.vaillant_noise_reduction_active").state == "off"


async def test_noise_reduction_state_follows_the_clock(hass, mqtt_mock):
    await _setup(hass)
    base = {f"{C}/{k}": v for k, v in HWC.items()}
    entity_id = "binary_sensor.vaillant_noise_reduction_active"
    path = "custom_components.ebusd_vaillant.binary_sensor.dt_util.now"
    before = datetime(2026, 10, 5, 13, 59, 30).astimezone()
    with patch(path, return_value=before):
        await _send(hass, {**base, f"{C}/SilentTimer_Monday": _slot("14:00", "16:00")})
        assert hass.states.get(entity_id).state == "off"
    inside = before + timedelta(minutes=1)
    with patch(path, return_value=inside):
        async_fire_time_changed(hass, datetime.now().astimezone() + timedelta(seconds=61))
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "on"


def test_noise_schedule_unknown_until_all_slots_are_seen():
    schedule = NoiseSchedule()
    three = {"slotcount": {"value": 3}}
    schedule.update("SilentTimer_Monday", {**_slot("00:00", "08:00", 0), **three})
    monday = datetime(2026, 10, 5, 15, 0)
    assert schedule.active_at(monday) is None  # 1 of 3 slots: no claim yet
    schedule.update("SilentTimer_Monday", {**_slot("14:00", "16:00", 1), **three})
    schedule.update("SilentTimer_Monday", {**_slot("18:30", "24:00", 2), **three})
    assert schedule.active_at(monday) is True


async def test_noise_slots_are_requested_one_by_one(hass, mqtt_mock, mqtt_client_mock):
    with patch("custom_components.ebusd_vaillant.binary_sensor.NOISE_REQUEST_INTERVAL", 0):
        await _setup(hass)
        base = {f"{C}/{k}": v for k, v in HWC.items()}
        three = {"slotcount": {"value": 3}}
        msgs = {
            **base,
            f"{C}/SilentTimer_Monday": {**_slot("00:00", "08:00", 0), **three},
            f"{C}/SilentTimer_Tuesday": {**_slot("00:00", "08:00", 0), **three},
        }
        await _send(hass, msgs)
        await hass.async_block_till_done(wait_background_tasks=True)
    gets = [
        (c.args[0], c.args[1].decode() if isinstance(c.args[1], bytes) else c.args[1])
        for c in mqtt_client_mock.publish.call_args_list
        if "SilentTimer" in c.args[0]
    ]
    for day in ("Monday", "Tuesday"):
        topic = f"{C}/SilentTimer_{day}/get"
        assert [p for t, p in gets if t == topic][:3] == ["0", "1", "2"]


async def test_noise_schedule_completes_from_the_slot_answers(hass, mqtt_mock):
    await _setup(hass)
    base = {f"{C}/{k}": v for k, v in HWC.items()}
    three = {"slotcount": {"value": 3}}
    entity_id = "binary_sensor.vaillant_noise_reduction_active"
    monday = datetime(2026, 10, 5, 15, 0).astimezone()
    with patch("custom_components.ebusd_vaillant.binary_sensor.dt_util.now", return_value=monday):
        await _send(
            hass,
            {**base, f"{C}/SilentTimer_Monday": {**_slot("00:00", "08:00", 0), **three}},
            times=1,
        )
        await _send(
            hass, {f"{C}/SilentTimer_Monday": {**_slot("18:30", "24:00", 2), **three}}, times=1
        )
        assert hass.states.get(entity_id).state == "unknown"  # slot 1 still missing
        await _send(
            hass, {f"{C}/SilentTimer_Monday": {**_slot("14:00", "16:00", 1), **three}}, times=1
        )
        state = hass.states.get(entity_id)
        assert state.state == "on"
        assert state.attributes["schedule"]["Monday"] == [
            "00:00-08:00",
            "14:00-16:00",
            "18:30-24:00",
        ]
