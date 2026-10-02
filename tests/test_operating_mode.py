"""Operating mode sensor and electricity split by mode (heating / hot water / standby)."""

import json
from datetime import timedelta

import pytest
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    mock_restore_cache_with_extra_data,
)

from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import DiscoveredOperatingMode, _analyze

MODE = "sensor.vaillant_heat_pump_operating_mode"
HEAT = "sensor.vaillant_heat_pump_electrical_energy_heating"
HW = "sensor.vaillant_heat_pump_electrical_energy_hot_water"
STANDBY = "sensor.vaillant_heat_pump_electrical_energy_standby"


def _status01(pumpstate: str) -> str:
    return json.dumps({"temp": {"value": 30}, "pumpstate": {"value": pumpstate}})


def _power(value: float) -> str:
    return json.dumps({"value": {"value": value}})


def test_discovery_with_power_in_watts():
    by_device = {
        "hmu": {
            "Status01": {"pumpstate": {"value": "off"}},
            "RunDataElectricPowerConsumption": {"value": {"value": 450}},
        }
    }
    mode = next(e for e in _analyze(by_device, "ebusd") if isinstance(e, DiscoveredOperatingMode))
    assert mode.device_key == "hmu"
    assert mode.power.read_topic == "ebusd/hmu/RunDataElectricPowerConsumption"
    assert mode.power_factor == 0.001


def test_no_operating_mode_without_signals():
    by_device = {"ctlv3": {"Z1OpMode": {"value": {"value": "auto"}}}}
    assert not any(isinstance(e, DiscoveredOperatingMode) for e in _analyze(by_device, "ebusd"))


async def _setup(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _send(hass, topic: str, raw: str) -> None:
    async_fire_mqtt_message(hass, f"ebusd/hmu/{topic}", raw)
    await hass.async_block_till_done()


async def _start(hass, freezer) -> None:
    freezer.move_to("2026-10-02 12:00:00+00:00")
    await _setup(hass)
    # first round discovers the entities, second feeds them
    for _ in range(2):
        await _send(hass, "Status01", _status01("on"))
        await _send(hass, "PowerConsumptionHmu", _power(1.0))


def _kwh(hass, entity_id: str) -> float:
    return float(hass.states.get(entity_id).state)


async def test_operating_mode_follows_signals(
    hass, mqtt_mock, freezer, entity_registry_enabled_by_default
):
    await _start(hass, freezer)
    assert hass.states.get(MODE).state == "heating"
    await _send(hass, "Status01", _status01("hwc"))
    assert hass.states.get(MODE).state == "hot_water"
    await _send(hass, "Status00", json.dumps({"defrost": {"value": "on"}}))
    assert hass.states.get(MODE).state == "defrost"


async def test_energy_split_by_mode(hass, mqtt_mock, freezer, entity_registry_enabled_by_default):
    await _start(hass, freezer)  # heating at 1.0 kW from 12:00

    freezer.tick(timedelta(minutes=30))
    await _send(hass, "PowerConsumptionHmu", _power(1.0))  # +0.5 kWh heating
    freezer.tick(timedelta(minutes=30))
    await _send(hass, "Status01", _status01("hwc"))  # +0.5 kWh heating, switch to hot water
    await _send(hass, "PowerConsumptionHmu", _power(2.0))
    freezer.tick(timedelta(minutes=15))
    await _send(hass, "PowerConsumptionHmu", _power(2.0))  # +0.5 kWh hot water
    await _send(hass, "Status01", _status01("off"))  # idle
    await _send(hass, "PowerConsumptionHmu", _power(0.05))
    freezer.tick(timedelta(minutes=20))
    await _send(hass, "PowerConsumptionHmu", _power(0.05))  # +0.0167 kWh standby

    assert _kwh(hass, HEAT) == pytest.approx(1.0)
    assert _kwh(hass, HW) == pytest.approx(0.5)
    assert _kwh(hass, STANDBY) == pytest.approx(0.05 / 3, abs=1e-5)


async def test_long_gap_is_not_integrated(
    hass, mqtt_mock, freezer, entity_registry_enabled_by_default
):
    await _start(hass, freezer)
    freezer.tick(timedelta(hours=2))  # e.g. ebusd was down
    await _send(hass, "PowerConsumptionHmu", _power(1.0))
    assert _kwh(hass, HEAT) == 0.0


async def test_energy_restored_after_restart(
    hass, mqtt_mock, freezer, entity_registry_enabled_by_default
):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(HEAT, "12.5"),
                {"native_value": 12.5, "native_unit_of_measurement": "kWh"},
            )
        ],
    )
    await _start(hass, freezer)
    assert _kwh(hass, HEAT) == 12.5
    freezer.tick(timedelta(minutes=6))
    await _send(hass, "PowerConsumptionHmu", _power(1.0))
    assert _kwh(hass, HEAT) == pytest.approx(12.6)
