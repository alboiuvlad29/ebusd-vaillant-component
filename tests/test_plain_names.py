"""Plain-language names (no "veto"), stable entity IDs, boost attributes and auto-off."""

import json
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.ebusd_vaillant.const import DOMAIN

P = "ebusd/ctlv3"


def _v(value) -> str:
    return json.dumps({"value": {"value": value}})


MSGS = {
    f"{P}/Z1DayTemp": 20.5,
    f"{P}/Z1RoomTemp": 21.0,
    f"{P}/Z1HolidayStartPeriod": "01.01.2015",
    f"{P}/Z1HolidayEndPeriod": "01.01.2015",
    f"{P}/Z1QuickVetoTemp": 22.5,
    f"{P}/Z1QuickVetoDuration": 3,
    f"{P}/Z1QuickVetoEndDate": "01.01.2015",
    f"{P}/Z1QuickVetoEndTime": "00:00:00",
    f"{P}/Z1OpMode": "auto",
    f"{P}/HwcTempDesired": 50,
    f"{P}/HwcStorageTemp": 48,
    f"{P}/HwcHolidayStartPeriod": "01.01.2015",
    f"{P}/HwcHolidayEndPeriod": "01.01.2015",
    f"{P}/HwcSFMode": "auto",
    f"{P}/HwcOpMode": "auto",
}


async def _setup(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in MSGS.items():
            async_fire_mqtt_message(hass, topic, _v(value))
        await hass.async_block_till_done()


async def _fire(hass, name: str, value) -> None:
    async_fire_mqtt_message(hass, f"{P}/{name}", _v(value))
    await hass.async_block_till_done()


async def test_names_without_veto_and_unchanged_entity_ids(hass, mqtt_mock):
    await _setup(hass)
    expected = {
        "switch.vaillant_zone_1_quick_veto": "Vaillant Zone 1 Heating boost",
        "switch.vaillant_zone_1_away_mode": "Vaillant Zone 1 Away",
        "switch.vaillant_hot_water_boost": "Vaillant Hot Water Boost",
        "switch.vaillant_hot_water_away_mode": "Vaillant Hot Water Away",
        "datetime.vaillant_zone_1_quick_veto_end": "Vaillant Zone 1 Heating boost end",
    }
    for entity_id, name in expected.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["friendly_name"] == name
        assert "veto" not in name.lower()


async def test_heating_boost_attributes_and_auto_off(hass, mqtt_mock, freezer):
    freezer.move_to("2026-10-02 12:00:00")
    await _setup(hass)
    switch = "switch.vaillant_zone_1_quick_veto"
    state = hass.states.get(switch)
    assert state.state == "off"
    assert state.attributes["boost_temperature"] == 22.5
    assert state.attributes["boost_duration_hours"] == 3
    assert state.attributes["boost_ends_at"] is None

    await _fire(hass, "Z1QuickVetoEndDate", "02.10.2026")
    await _fire(hass, "Z1QuickVetoEndTime", "15:00:00")
    state = hass.states.get(switch)
    assert state.state == "on"
    assert state.attributes["boost_ends_at"] == "2026-10-02T15:00:00"

    freezer.move_to("2026-10-02 15:00:05")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    state = hass.states.get(switch)
    assert state.state == "off"
    assert state.attributes["boost_ends_at"] is None


async def test_water_heater_boost_active_attribute(hass, mqtt_mock):
    await _setup(hass)
    assert hass.states.get("water_heater.vaillant_hot_water").attributes["boost_active"] is False
    await _fire(hass, "HwcSFMode", "load")
    assert hass.states.get("water_heater.vaillant_hot_water").attributes["boost_active"] is True
    assert hass.states.get("switch.vaillant_hot_water_boost").state == "on"
    await _fire(hass, "HwcSFMode", "auto")  # charge finished
    assert hass.states.get("switch.vaillant_hot_water_boost").state == "off"


async def test_preset_names_translated(hass):
    from homeassistant.helpers.translation import async_get_translations

    t = await async_get_translations(hass, "en", "entity", [DOMAIN])
    key = f"component.{DOMAIN}.entity.climate.ebusd_zone.state_attributes.preset_mode.state"
    assert t[f"{key}.boost"] == "Heating boost"
    assert t[f"{key}.away"] == "Away"


async def test_boost_timer_rescheduled_when_end_changes(hass, mqtt_mock, freezer):
    freezer.move_to("2026-10-02 12:00:00")
    await _setup(hass)
    await _fire(hass, "Z1QuickVetoEndDate", "02.10.2026")
    await _fire(hass, "Z1QuickVetoEndTime", "13:00:00")
    await _fire(hass, "Z1QuickVetoEndTime", "16:00:00")  # extended
    freezer.move_to("2026-10-02 13:00:05")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("switch.vaillant_zone_1_quick_veto").state == "on"
    freezer.tick(timedelta(hours=3))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("switch.vaillant_zone_1_quick_veto").state == "off"
