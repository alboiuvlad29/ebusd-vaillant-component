"""Mode names as shown on the sensoCOMFORT: Time controlled / Manual / Off."""

import json
import pathlib

import pytest
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import DOMAIN

TRANSLATIONS = pathlib.Path(__file__).parent.parent / (
    "custom_components/ebusd_vaillant/translations"
)


async def test_english_mode_names(hass):
    t = await async_get_translations(hass, "en", "entity", [DOMAIN])
    zone = f"component.{DOMAIN}.entity.climate.ebusd_zone.state"
    hwc = f"component.{DOMAIN}.entity.water_heater.ebusd_water_heater.state"
    assert t[f"{zone}.auto"] == "Time controlled"
    assert t[f"{zone}.heat"] == "Manual"
    assert t[f"{zone}.off"] == "Off"
    assert t[f"{hwc}.auto"] == "Time controlled"
    assert t[f"{hwc}.day"] == "Manual"
    assert t[f"{hwc}.manual"] == "Manual"
    assert t[f"{hwc}.off"] == "Off"
    assert t[f"{hwc}.boost"] == "Boost"


@pytest.mark.parametrize("path", sorted(TRANSLATIONS.glob("*.json")), ids=lambda p: p.stem)
def test_every_language_names_all_modes(path):
    entity = json.loads(path.read_text())["entity"]
    assert set(entity["climate"]["ebusd_zone"]["state"]) >= {"auto", "heat", "off"}
    assert set(entity["water_heater"]["ebusd_water_heater"]["state"]) >= {
        "auto",
        "day",
        "manual",
        "off",
        "boost",
    }


async def test_zone_name_and_entity_id_unchanged(hass, mqtt_mock):
    """The translation key only renames states; the entity keeps its device name."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    msgs = {
        "ebusd/ctlv3/Z1DayTemp": 20.5,
        "ebusd/ctlv3/Z1RoomTemp": 21.0,
        "ebusd/ctlv3/Z1OpMode": "auto",
    }
    for _ in range(2):
        for topic, value in msgs.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
    state = hass.states.get("climate.vaillant_zone_1")
    assert state is not None
    assert state.attributes["friendly_name"] == "Vaillant Zone 1"
