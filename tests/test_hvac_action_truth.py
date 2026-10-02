"""Truthful hvac_action from fast heat pump signals instead of the stale status code."""

import json

import pytest
from homeassistant.components.climate import HVACAction
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.activity import (
    ACTIVITY_COOLING,
    ACTIVITY_DEFROST,
    ACTIVITY_HEATING,
    ACTIVITY_HOT_WATER,
    ACTIVITY_IDLE,
    compute_activity,
)
from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import DiscoveredClimate, _analyze


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, None),
        ({"statuscode": "standby"}, ACTIVITY_IDLE),
        ({"statuscode": "heat_compressor_active"}, ACTIVITY_HEATING),
        ({"statuscode": "cool_compressor_active"}, ACTIVITY_COOLING),
        ({"statuscode": "heat_compressor_shutdown"}, ACTIVITY_IDLE),
        ({"pumpstate": "off", "statuscode": "heat_compressor_active"}, ACTIVITY_IDLE),
        ({"pumpstate": "on", "statuscode": "standby"}, ACTIVITY_HEATING),
        ({"pumpstate": "overrun"}, ACTIVITY_HEATING),
        ({"pumpstate": "hwc", "statuscode": "heat_compressor_active"}, ACTIVITY_HOT_WATER),
        ({"warmwater": "on", "power": 46}, ACTIVITY_HOT_WATER),
        ({"warmwater": "off", "power": 46, "pumpstate": "off"}, ACTIVITY_HEATING),
        ({"power": 0, "pumpstate": "on"}, ACTIVITY_IDLE),
        ({"power": 30, "statuscode": "cool_compressor_active"}, ACTIVITY_COOLING),
        ({"compressor": "on"}, ACTIVITY_HEATING),
        ({"compressor": "off", "pumpstate": "on"}, ACTIVITY_IDLE),
        ({"heating_bit": 1}, ACTIVITY_HEATING),
        ({"defrost": "on", "warmwater": "on", "power": 40}, ACTIVITY_DEFROST),
    ],
)
def test_compute_activity_priorities(values, expected):
    assert compute_activity(values) == expected


def test_activity_topics_discovered_from_hmu():
    by_device = {
        "ctlv3": {
            "Z1OpMode": {"value": {"value": "manual"}},
            "Z1RoomTemp": {"value": {"value": 21.0}},
            "Z1ManualTemp": {"value": {"value": 20.5}},
            "Z1Status": {"value": {"value": "heating"}},
        },
        "hmu": {
            "RunDataStatuscode": {"value": {"value": "standby"}},
            "Status01": {"temp": {"value": 30}, "pumpstate": {"value": "off"}},
            "Status07": {
                "heatermain_b7_warmwater": {"value": "off"},
                "power": {"value": 0},
            },
        },
    }
    z1 = next(e for e in _analyze(by_device, "ebusd") if isinstance(e, DiscoveredClimate))
    assert z1.zone_status.read_topic == "ebusd/ctlv3/Z1Status"
    assert z1.activity.pumpstate.read_topic == "ebusd/hmu/Status01"
    assert z1.activity.pumpstate.field == "pumpstate.value"
    assert z1.activity.warmwater.field == "heatermain_b7_warmwater.value"
    assert z1.activity.power.field == "power.value"
    assert z1.activity.statuscode.read_topic == "ebusd/hmu/RunDataStatuscode"
    assert z1.activity.defrost is None


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

ZONE = {
    "ebusd/ctlv3/Z1DayTemp": {"value": {"value": 20.5}},
    "ebusd/ctlv3/Z1RoomTemp": {"value": {"value": 21.0}},
    "ebusd/hmu/YieldCooling": {"value": {"value": 0}},
    "ebusd/ctlv3/Z1OpMode": {"value": {"value": "day"}},
}


async def _setup(hass, extra: dict | None = None) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    msgs = {**(extra or {}), **ZONE}
    for _ in range(2):
        for topic, payload in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps(payload))
        await hass.async_block_till_done()


async def _send(hass, topic: str, payload: dict) -> None:
    async_fire_mqtt_message(hass, f"ebusd/{topic}", json.dumps(payload))
    await hass.async_block_till_done()


def _action(hass) -> str:
    return hass.states.get("climate.vaillant_zone_1").attributes["hvac_action"]


def _status01(pumpstate: str) -> dict:
    return {"temp": {"value": 30}, "pumpstate": {"value": pumpstate}}


def _status07(warmwater: str, power: float) -> dict:
    return {
        "heatermain_b7_warmwater": {"value": warmwater},
        "heatermain_b3_heating": {"value": "off"},
        "power": {"value": power},
    }


async def test_manual_mode_idle_heat_pump_is_idle(hass, mqtt_mock):
    """The reported quirk: Manual mode showed 'heating' while the heat pump idled."""
    await _setup(hass, {"ebusd/hmu/RunDataStatuscode": {"value": {"value": "standby"}}})
    assert _action(hass) == HVACAction.IDLE


async def test_no_signals_keeps_mode_fallback(hass, mqtt_mock):
    await _setup(hass)
    assert _action(hass) == HVACAction.HEATING


async def test_stale_status_code_loses_to_pumpstate(hass, mqtt_mock):
    await _setup(
        hass,
        {
            "ebusd/hmu/RunDataStatuscode": {"value": {"value": "heat_compressor_active"}},
            "ebusd/hmu/Status01": _status01("off"),
        },
    )
    assert _action(hass) == HVACAction.IDLE
    await _send(hass, "hmu/Status01", _status01("on"))
    assert _action(hass) == HVACAction.HEATING


async def test_hot_water_run_ground_truth(hass, mqtt_mock):
    """The hot-water run of 2026-10-02, 15:05-15:34, replayed in order."""
    await _setup(
        hass,
        {
            "ebusd/hmu/RunDataStatuscode": {"value": {"value": "heat_compressor_active"}},
            "ebusd/hmu/Status01": _status01("on"),
            "ebusd/hmu/Status07": _status07("off", 30),
        },
    )
    assert _action(hass) == HVACAction.HEATING  # space heating before the run

    await _send(hass, "hmu/Status07", _status07("on", 22))  # 15:05:43 warm-water flag
    assert _action(hass) == HVACAction.IDLE
    await _send(hass, "hmu/Status01", _status01("hwc"))  # 15:06:43 pumpstate=hwc
    await _send(hass, "hmu/Status07", _status07("on", 46))  # compressor 22 to 46 %
    assert _action(hass) == HVACAction.IDLE

    await _send(hass, "hmu/Status07", _status07("off", 0))  # 15:25:50 flag off
    await _send(hass, "hmu/Status01", _status01("off"))
    # status code still claims activity until 15:34; the fresh signals win
    await _send(hass, "hmu/RunDataStatuscode", {"value": {"value": "heat_compressor_active"}})
    assert _action(hass) == HVACAction.IDLE


async def test_defrost(hass, mqtt_mock):
    await _setup(hass, {"ebusd/hmu/Status01": _status01("on")})
    await _send(hass, "hmu/Status00", {"defrost": {"value": "on"}, "compressorstate": {"value": 1}})
    assert _action(hass) == HVACAction.DEFROSTING


async def test_zone_not_asking_for_heat_is_idle(hass, mqtt_mock):
    await _setup(
        hass,
        {
            "ebusd/hmu/Status01": _status01("on"),
            "ebusd/ctlv3/Z1Status": {"value": {"value": "off"}},
        },
    )
    assert _action(hass) == HVACAction.IDLE
    await _send(hass, "ctlv3/Z1Status", {"value": {"value": "heating"}})
    assert _action(hass) == HVACAction.HEATING


async def test_off_mode_stays_off(hass, mqtt_mock):
    await _setup(hass, {"ebusd/hmu/Status01": _status01("on")})
    await _send(hass, "ctlv3/Z1OpMode", {"value": {"value": "off"}})
    assert _action(hass) == HVACAction.OFF
