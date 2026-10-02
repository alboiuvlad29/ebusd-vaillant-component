"""Room sensors: the sensoCOMFORT's own sensor, VR 92 remote units and zone humidity."""

import json

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import DOMAIN
from custom_components.ebusd_vaillant.discovery import DiscoveredSensor, _analyze

C = "ebusd/ctlv3"
MSGS = {
    f"{C}/RoomTemp": 22.9,
    f"{C}/RoomHumidity": 49,
    f"{C}/VR92Addr1RoomTemp": 23.0,
    f"{C}/VR92Addr1RoomHumidity": 49,
    f"{C}/VR92Addr2RoomTemp": None,  # defined, but no second remote connected
    f"{C}/Z1RoomHumidity": 51,
    f"{C}/Z1ManualTemp": 20.5,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1OpMode": "manual",
}


def test_room_sensor_discovery():
    by_device = {"ctlv3": {t.rsplit("/", 1)[1]: {"value": {"value": v}} for t, v in MSGS.items()}}
    sensors = {
        e.key: e
        for e in _analyze(by_device, "ebusd")
        if isinstance(e, DiscoveredSensor) and e.unique_id_prefix in ("ebusd_room", "ebusd_zone")
    }
    assert set(sensors) >= {
        "ctlv3_room_temperature",
        "ctlv3_room_humidity",
        "ctlv3_vr92_1_temperature",
        "ctlv3_vr92_1_humidity",
        "ctlv3_zone1_room_humidity",
    }
    assert "ctlv3_vr92_2_temperature" not in sensors
    assert sensors["ctlv3_vr92_1_temperature"].translation_placeholders == {"number": "1"}
    assert sensors["ctlv3_zone1_room_humidity"].device_key == "ctlv3_zone1"


async def test_room_sensor_entities(hass, mqtt_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in MSGS.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()

    expected = {
        "sensor.vaillant_controller_room_temperature": ("22.9", "Room temperature"),
        "sensor.vaillant_controller_room_humidity": ("49.0", "Room humidity"),
        "sensor.vaillant_controller_remote_1_room_temperature": (
            "23.0",
            "Remote 1 room temperature",
        ),
        "sensor.vaillant_zone_1_room_humidity": ("51.0", "Room humidity"),
    }
    for entity_id, (value, name) in expected.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == value
        assert state.attributes["friendly_name"].endswith(name)
