"""Hot water extras: boost off the mode list, boost button, status and legionella sensors."""

import json

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import CONF_HWC_BOOST_AS_MODE, DOMAIN

C = "ebusd/ctlv3"
MSGS = {
    f"{C}/HwcTempDesired": 50,
    f"{C}/HwcStorageTemp": 48,
    f"{C}/HwcSFMode": "auto",
    f"{C}/HwcStatus": "standby",
    f"{C}/HwcReheatingActive": "no",
    f"{C}/HwcLegionellaDay": "Monday",
    f"{C}/HwcLegionellaTime": "02:00",
    f"{C}/HwcOpMode": "manual",
}
WH = "water_heater.vaillant_hot_water"


async def _setup(hass, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options=options or {})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in MSGS.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
    return entry


async def _send(hass, name: str, value) -> None:
    async_fire_mqtt_message(hass, f"{C}/{name}", json.dumps({"value": {"value": value}}))
    await hass.async_block_till_done()


async def test_boost_is_not_a_mode(hass, mqtt_mock):
    await _setup(hass)
    state = hass.states.get(WH)
    assert state.attributes["operation_list"] == ["auto", "manual", "off"]
    await _send(hass, "HwcSFMode", "load")
    state = hass.states.get(WH)
    assert state.state == "manual"  # the real mode stays visible
    assert state.attributes["boost_active"] is True
    assert hass.states.get("switch.vaillant_hot_water_boost").state == "on"


async def test_legacy_option_keeps_boost_mode(hass, mqtt_mock):
    await _setup(hass, {CONF_HWC_BOOST_AS_MODE: True})
    assert hass.states.get(WH).attributes["operation_list"] == ["auto", "manual", "off", "boost"]
    await _send(hass, "HwcSFMode", "load")
    assert hass.states.get(WH).state == "boost"


async def test_boost_button(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    button = "button.vaillant_hot_water_start_boost"
    assert hass.states.get(button) is not None
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    topics = {c.args[0]: c.args[1] for c in mqtt_client_mock.publish.call_args_list}
    assert topics[f"{C}/HwcSFMode/set"] in ("load", b"load")


async def test_hot_water_sensors(hass, mqtt_mock):
    await _setup(hass)
    assert hass.states.get("sensor.vaillant_hot_water_hot_water_status").state == "standby"
    assert hass.states.get("sensor.vaillant_hot_water_legionella_protection_day").state == "Monday"
    assert hass.states.get("sensor.vaillant_hot_water_legionella_protection_time").state == "02:00"
    reheating = "binary_sensor.vaillant_hot_water_reheating_active"
    assert hass.states.get(reheating).state == "off"
    await _send(hass, "HwcReheatingActive", "yes")
    assert hass.states.get(reheating).state == "on"


async def test_options_flow_has_legacy_boost_option(hass, mqtt_mock):
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert CONF_HWC_BOOST_AS_MODE in {str(k) for k in result["data_schema"].schema}
