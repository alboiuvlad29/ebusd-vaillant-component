"""Zone extras: effective target temperature, setback temperature, active time slot."""

import json

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import (
    DiscoveredEffectiveTarget,
    DiscoveredFlag,
    DiscoveredSensor,
    _analyze,
)

C = "ebusd/ctlv3"
ZONE = {
    f"{C}/Z1ManualTemp": 20.5,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1TempDesired": 19.0,
    f"{C}/Z1SetbackTemp": 17.0,
    f"{C}/Z1TimeSlotActive": "no",
    f"{C}/Z1OpMode": "auto",
}


def _by_device() -> dict:
    return {"ctlv3": {t.rsplit("/", 1)[1]: {"value": {"value": v}} for t, v in ZONE.items()}}


def test_zone_extras_discovered_on_zone_device():
    entities = _analyze(_by_device(), "ebusd")
    sensors = {e.key: e for e in entities if isinstance(e, DiscoveredSensor)}
    desired = next(e for e in entities if isinstance(e, DiscoveredEffectiveTarget))
    assert desired.key == "ctlv3_zone1_temp_desired"
    assert desired.zone.temp_desired.read_topic == f"{C}/Z1TempDesired"
    assert desired.device_key == "ctlv3_zone1"
    assert sensors["ctlv3_zone1_setback_temp"].topic.read_topic == f"{C}/Z1SetbackTemp"
    flag = next(e for e in entities if isinstance(e, DiscoveredFlag))
    assert flag.topic.read_topic == f"{C}/Z1TimeSlotActive"


def test_setback_temp_is_not_a_second_target():
    """Z1SetbackTemp (new definitions) must not turn the zone into a day/setback range."""
    from custom_components.ebusd_vaillant.discovery import DiscoveredClimate

    z1 = next(e for e in _analyze(_by_device(), "ebusd") if isinstance(e, DiscoveredClimate))
    assert z1.target_temperature.read_topic == f"{C}/Z1ManualTemp"
    assert z1.target_temperature_low is None


def test_no_extras_with_old_definitions():
    by_device = {
        "ctlv3": {
            "Z1DayTemp": {"value": {"value": 20.5}},
            "Z1RoomTemp": {"value": {"value": 21.0}},
            "Z1OpMode": {"value": {"value": "day"}},
        }
    }
    entities = _analyze(by_device, "ebusd")
    assert not [e for e in entities if isinstance(e, DiscoveredFlag)]
    assert not [e for e in entities if isinstance(e, DiscoveredSensor) and "zone1" in e.key]


async def test_zone_extra_entities(hass, mqtt_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in ZONE.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()

    desired = hass.states.get("sensor.vaillant_zone_1_effective_target_temperature")
    assert desired.state == "19.0"
    assert desired.attributes["friendly_name"] == "Vaillant Zone 1 Effective target temperature"
    assert hass.states.get("sensor.vaillant_zone_1_setback_temperature").state == "17.0"
    slot = hass.states.get("binary_sensor.vaillant_zone_1_time_slot_active")
    assert slot.state == "off"

    async_fire_mqtt_message(hass, f"{C}/Z1TimeSlotActive", json.dumps({"value": {"value": "yes"}}))
    async_fire_mqtt_message(hass, f"{C}/Z1TempDesired", json.dumps({"value": {"value": 21.5}}))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.vaillant_zone_1_time_slot_active").state == "on"
    assert hass.states.get("sensor.vaillant_zone_1_effective_target_temperature").state == "21.5"
    assert hass.states.get("climate.vaillant_zone_1").attributes["temperature"] == 21.5
