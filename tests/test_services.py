"""Services for automations: heating boost, away, hot water boost."""

import json

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)
from voluptuous import Invalid

from custom_components.ebusd_vaillant.const import DOMAIN

C = "ebusd/ctlv3"
MSGS = {
    f"{C}/Z1DayTemp": 20.5,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1HolidayStartPeriod": "01.01.2015",
    f"{C}/Z1HolidayEndPeriod": "01.01.2015",
    f"{C}/Z1QuickVetoTemp": 21,
    f"{C}/Z1QuickVetoDuration": 3,
    f"{C}/Z1QuickVetoEndDate": "01.01.2015",
    f"{C}/Z1QuickVetoEndTime": "00:00:00",
    f"{C}/Z1OpMode": "auto",
    f"{C}/HwcTempDesired": 50,
    f"{C}/HwcSFMode": "auto",
    f"{C}/HwcHolidayStartPeriod": "01.01.2015",
    f"{C}/HwcHolidayEndPeriod": "01.01.2015",
    f"{C}/HwcOpMode": "auto",
}
ZONE = "climate.vaillant_zone_1"
HWC = "water_heater.vaillant_hot_water"


@pytest.fixture
async def entry(hass, mqtt_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in MSGS.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
    return entry


async def _call(hass, mqtt_client_mock, service: str, data: dict) -> dict[str, str]:
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    out = {}
    for c in mqtt_client_mock.publish.call_args_list:
        payload = c.args[1]
        out[c.args[0]] = payload.decode() if isinstance(payload, bytes) else str(payload)
    return out


async def test_set_quick_veto_with_duration(hass, entry, mqtt_client_mock):
    out = await _call(
        hass,
        mqtt_client_mock,
        "set_quick_veto",
        {"entity_id": ZONE, "temperature": 22.5, "duration_hours": 1.5},
    )
    assert out[f"{C}/Z1QuickVetoTemp/set"] == "22.5"
    assert out[f"{C}/Z1QuickVetoDuration/set"] == "1.5"


async def test_set_quick_veto_default_duration(hass, entry, mqtt_client_mock):
    out = await _call(
        hass, mqtt_client_mock, "set_quick_veto", {"entity_id": ZONE, "temperature": 22}
    )
    assert out[f"{C}/Z1QuickVetoDuration/set"] == "3"


async def test_cancel_quick_veto(hass, entry, mqtt_client_mock):
    out = await _call(hass, mqtt_client_mock, "cancel_quick_veto", {"entity_id": ZONE})
    assert out[f"{C}/Z1QuickVetoDuration/set"] == "0"
    assert out[f"{C}/Z1QuickVetoEndDate/set"] == "01.01.2015"


async def test_set_and_cancel_away_for_zone_and_hot_water(hass, entry, mqtt_client_mock):
    out = await _call(
        hass,
        mqtt_client_mock,
        "set_away",
        {"entity_id": [ZONE, HWC], "start_date": "2026-12-20", "end_date": "2027-01-03"},
    )
    assert out[f"{C}/Z1HolidayStartPeriod/set"] == "20.12.2026"
    assert out[f"{C}/Z1HolidayEndPeriod/set"] == "03.01.2027"
    assert out[f"{C}/HwcHolidayStartPeriod/set"] == "20.12.2026"
    assert out[f"{C}/HwcHolidayEndPeriod/set"] == "03.01.2027"

    out = await _call(hass, mqtt_client_mock, "cancel_away", {"entity_id": [ZONE, HWC]})
    assert out[f"{C}/Z1HolidayEndPeriod/set"] == "01.01.2015"
    assert out[f"{C}/HwcHolidayEndPeriod/set"] == "01.01.2015"


async def test_set_away_rejects_end_before_start(hass, entry):
    with pytest.raises(Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_away",
            {"entity_id": ZONE, "start_date": "2027-01-03", "end_date": "2026-12-20"},
            blocking=True,
        )


@pytest.mark.parametrize(("enable", "payload"), [(True, "load"), (False, "auto")])
async def test_hot_water_boost(hass, entry, mqtt_client_mock, enable, payload):
    out = await _call(
        hass, mqtt_client_mock, "hot_water_boost", {"entity_id": HWC, "enable": enable}
    )
    assert out[f"{C}/HwcSFMode/set"] == payload


async def test_wrong_entity_kind_is_rejected(hass, entry):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "hot_water_boost", {"entity_id": ZONE}, blocking=True
        )


async def test_renamed_entity_still_reachable(hass, entry, mqtt_client_mock):
    er.async_get(hass).async_update_entity(ZONE, new_entity_id="climate.ground_floor")
    await hass.async_block_till_done()
    out = await _call(
        hass,
        mqtt_client_mock,
        "set_quick_veto",
        {"entity_id": "climate.ground_floor", "temperature": 23},
    )
    assert out[f"{C}/Z1QuickVetoTemp/set"] == "23.0"


async def test_services_removed_on_unload(hass, entry):
    assert hass.services.has_service(DOMAIN, "set_quick_veto")
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "set_quick_veto")
