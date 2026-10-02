"""Faults and health: current error sensors with Repairs, low pressure, eBUS connection."""

import json

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import CONF_LOW_PRESSURE, DOMAIN
from custom_components.ebusd_vaillant.discovery import (
    DiscoveredErrorSensor,
    DiscoveredPressureMonitor,
    DiscoveredSensor,
    _analyze,
)
from custom_components.ebusd_vaillant.sensor import error_codes

NO_ERRORS = {f"error{s}": {"value": None} for s in ["", "_1", "_2", "_3", "_4"]}


def _errors(*codes) -> dict:
    payload = dict(NO_ERRORS)
    for i, code in enumerate(codes):
        payload["error" if i == 0 else f"error_{i}"] = {"value": code}
    return payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (NO_ERRORS, []),
        (_errors(22), ["22"]),
        (_errors(22, 535), ["22", "535"]),
        ({"error": {"value": "-"}}, []),
        ("garbage", []),
    ],
)
def test_error_codes(payload, expected):
    assert error_codes(payload) == expected


def test_discovery_of_health_entities():
    by_device = {
        "ctlv3": {
            "Currenterror": NO_ERRORS,
            "WaterPressure": {"value": {"value": 2.1}},
        },
        "hmu": {
            "Currenterror": NO_ERRORS,
            "Status07": {
                "displaypressure": {"value": 2.0},
                "heatermain_b5_pressureloss": {"value": "off"},
            },
        },
    }
    entities = _analyze(by_device, "ebusd")
    errors = sorted(e.key for e in entities if isinstance(e, DiscoveredErrorSensor))
    assert errors == ["ctlv3_current_error", "hmu_current_error"]
    monitor = next(e for e in entities if isinstance(e, DiscoveredPressureMonitor))
    assert monitor.pressure.read_topic == "ebusd/hmu/Status07"  # the fresher signal
    assert monitor.pressure.field == "displaypressure.value"
    assert monitor.pressure_loss.field == "heatermain_b5_pressureloss.value"
    pressure = [e for e in entities if isinstance(e, DiscoveredSensor) and "pressure" in e.key]
    assert [e.key for e in pressure] == ["ctlv3_pressure"]  # no duplicate from Status07


def test_status07_pressure_sensor_when_nothing_else():
    by_device = {"hmu": {"Status07": {"displaypressure": {"value": 2.0}}}}
    entities = _analyze(by_device, "ebusd")
    sensor = next(e for e in entities if isinstance(e, DiscoveredSensor))
    assert sensor.unique_id_prefix == "ebusd_pressure"
    assert sensor.topic.field == "displaypressure.value"


# ---------------------------------------------------------------------------
# Entities (enabled for the tests; disabled by default in real installs)
# ---------------------------------------------------------------------------


async def _setup(hass, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options=options or {})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _send(hass, topic: str, payload) -> None:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    for _ in range(2):  # first discovers, second updates the new entity
        async_fire_mqtt_message(hass, f"ebusd/{topic}", raw)
        await hass.async_block_till_done()


async def test_health_entities_disabled_by_default(hass, mqtt_mock):
    await _setup(hass)
    await _send(hass, "hmu/Currenterror", NO_ERRORS)
    await _send(hass, "ctlv3/WaterPressure", {"value": {"value": 2.1}})
    registry = er.async_get(hass)
    entries = {
        e.unique_id: e
        for e in registry.entities.values()
        if e.unique_id.startswith(("ebusd_current_error", "ebusd_low_pressure", "ebusd_conn"))
    }
    assert set(entries) == {
        "ebusd_current_error_hmu_current_error",
        "ebusd_low_pressure_ebusd_low_pressure",
        "ebusd_connected_ebusd",
    }
    assert all(e.disabled_by is er.RegistryEntryDisabler.INTEGRATION for e in entries.values())


async def test_current_error_sensor_and_repairs(
    hass, mqtt_mock, entity_registry_enabled_by_default
):
    await _setup(hass)
    await _send(hass, "hmu/Currenterror", NO_ERRORS)
    entity_id = "sensor.vaillant_heat_pump_current_error"
    assert hass.states.get(entity_id).state == "none"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "device_error_hmu_current_error") is None

    await _send(hass, "hmu/Currenterror", _errors(22))
    state = hass.states.get(entity_id)
    assert state.state == "22"
    assert state.attributes["codes"] == ["22"]
    issue = issues.async_get_issue(DOMAIN, "device_error_hmu_current_error")
    assert issue is not None
    assert issue.translation_placeholders["codes"] == "22"

    await _send(hass, "hmu/Currenterror", NO_ERRORS)
    assert hass.states.get(entity_id).state == "none"
    assert issues.async_get_issue(DOMAIN, "device_error_hmu_current_error") is None


async def test_low_pressure(hass, mqtt_mock, entity_registry_enabled_by_default):
    await _setup(hass, {CONF_LOW_PRESSURE: 1.8})
    await _send(hass, "ctlv3/WaterPressure", {"value": {"value": 2.1}})
    entity_id = "binary_sensor.vaillant_low_pressure"
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert state.attributes["threshold"] == 1.8

    await _send(hass, "ctlv3/WaterPressure", {"value": {"value": 1.6}})
    assert hass.states.get(entity_id).state == "on"
    await _send(hass, "ctlv3/WaterPressure", {"value": {"value": 0}})  # no reading
    assert hass.states.get(entity_id).state == "off"


async def test_pressure_loss_flag(hass, mqtt_mock, entity_registry_enabled_by_default):
    await _setup(hass)
    status07 = {"displaypressure": {"value": 2.2}, "heatermain_b5_pressureloss": {"value": "off"}}
    await _send(hass, "hmu/Status07", status07)
    entity_id = "binary_sensor.vaillant_low_pressure"
    assert hass.states.get(entity_id).state == "off"
    status07["heatermain_b5_pressureloss"] = {"value": "on"}
    await _send(hass, "hmu/Status07", status07)
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["pressure_loss"] is True


@pytest.mark.parametrize("fmt", ["plain", "json"])
async def test_ebus_connected(hass, mqtt_mock, entity_registry_enabled_by_default, fmt):
    await _setup(hass)

    def payload(value: bool) -> str:
        text = "true" if value else "false"
        return text if fmt == "plain" else json.dumps({"value": value})

    entity_id = "binary_sensor.vaillant_ebus_connected"
    assert hass.states.get(entity_id).state == "unknown"
    await _send(hass, "global/running", payload(True))
    await _send(hass, "global/signal", payload(True))
    assert hass.states.get(entity_id).state == "on"
    await _send(hass, "global/signal", payload(False))
    assert hass.states.get(entity_id).state == "off"


async def test_pressure_loss_flag_found_after_polled_pressure(
    hass, mqtt_mock, entity_registry_enabled_by_default
):
    """WaterPressure arrives first, Status07 later: the flag must still be watched."""
    await _setup(hass)
    await _send(hass, "ctlv3/WaterPressure", {"value": {"value": 2.1}})
    entity_id = "binary_sensor.vaillant_low_pressure"
    assert hass.states.get(entity_id).state == "off"

    status07 = {"displaypressure": {"value": 2.2}, "heatermain_b5_pressureloss": {"value": "off"}}
    await _send(hass, "hmu/Status07", status07)
    assert hass.states.get(entity_id).attributes["pressure"] == 2.2  # now the fast source
    status07["heatermain_b5_pressureloss"] = {"value": "on"}
    await _send(hass, "hmu/Status07", status07)
    assert hass.states.get(entity_id).state == "on"
