"""Poll priming: essentials fast (?1), the rest slow (?5), or everything fast, or off."""

import json

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import (
    CONF_POLL_PRIMING,
    CONF_PRIME_VALUES,
    DOMAIN,
    POLL_PRIMING_ALL,
    POLL_PRIMING_ESSENTIALS,
    POLL_PRIMING_OFF,
    poll_priming,
)

C = "ebusd/ctlv3"
H = "ebusd/hmu"
MSGS = {
    f"{C}/Z1DayTemp": 20.5,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1QuickVetoTemp": 21,
    f"{C}/Z1QuickVetoDuration": 3,
    f"{C}/Z1QuickVetoEndDate": "01.01.2015",
    f"{C}/Z1HolidayStartPeriod": "01.01.2015",
    f"{C}/Z1OpMode": "day",
    f"{C}/HwcTempDesired": 50,
    f"{C}/HwcStorageTemp": 48,
    f"{C}/HwcSFMode": "auto",
    f"{C}/HwcHolidayStartPeriod": "01.01.2015",
    f"{C}/HwcOpMode": "day",
    f"{H}/PowerConsumptionHmu": 0.4,
    f"{H}/YieldHcDay": 3.2,
    f"{H}/YieldHcMonth": 46,
    f"{H}/CopHc": 4.1,
}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, POLL_PRIMING_ESSENTIALS),
        ({CONF_PRIME_VALUES: True}, POLL_PRIMING_ALL),
        ({CONF_PRIME_VALUES: False}, POLL_PRIMING_OFF),
        ({CONF_PRIME_VALUES: False, CONF_POLL_PRIMING: POLL_PRIMING_ESSENTIALS}, "essentials"),
    ],
)
def test_legacy_option_mapping(options, expected):
    assert poll_priming(options) == expected


async def _gets(hass, mqtt_client_mock, options: dict) -> dict[str, str]:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mqtt_client_mock.publish.reset_mock()
    for topic, value in MSGS.items():
        async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
    async_fire_mqtt_message(
        hass, f"{H}/Status01", json.dumps({"temp": {"value": 30}, "pumpstate": {"value": "off"}})
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    gets: dict[str, str] = {}
    for c in mqtt_client_mock.publish.call_args_list:
        topic = c.args[0]
        if topic.endswith("/get"):
            payload = c.args[1]
            gets[topic[: -len("/get")]] = (
                payload.decode() if isinstance(payload, bytes) else str(payload)
            )
    return gets


async def test_essentials_fast_rest_slow(hass, mqtt_mock, mqtt_client_mock):
    gets = await _gets(hass, mqtt_client_mock, {CONF_POLL_PRIMING: POLL_PRIMING_ESSENTIALS})
    for name in [
        "Z1OpMode",
        "Z1DayTemp",
        "Z1RoomTemp",
        "Z1QuickVetoTemp",
        "Z1QuickVetoDuration",
        "HwcOpMode",
        "HwcTempDesired",
        "HwcSFMode",
        "HwcStorageTemp",
    ]:
        assert gets[f"{C}/{name}"] == "?1", name
    assert gets[f"{H}/PowerConsumptionHmu"] == "?1"
    assert gets[f"{H}/YieldHcDay"] == "?1"
    for topic in [
        f"{C}/Z1QuickVetoEndDate",
        f"{C}/Z1HolidayStartPeriod",
        f"{C}/HwcHolidayStartPeriod",
        f"{H}/YieldHcMonth",
        f"{H}/CopHc",
    ]:
        assert gets[topic] == "?5", topic
    assert f"{H}/Status01" not in gets  # overheard on the bus, never polled


async def test_everything_fast(hass, mqtt_mock, mqtt_client_mock):
    gets = await _gets(hass, mqtt_client_mock, {CONF_POLL_PRIMING: POLL_PRIMING_ALL})
    assert gets[f"{C}/Z1HolidayStartPeriod"] == "?1"
    assert gets[f"{H}/YieldHcMonth"] == "?1"
    assert set(gets.values()) == {"?1"}


@pytest.mark.parametrize(
    "options", [{CONF_POLL_PRIMING: POLL_PRIMING_OFF}, {CONF_PRIME_VALUES: False}]
)
async def test_off_publishes_nothing(hass, mqtt_mock, mqtt_client_mock, options):
    gets = await _gets(hass, mqtt_client_mock, options)
    assert gets == {}


async def test_options_flow_defaults_from_legacy_option(hass, mqtt_mock):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options={CONF_PRIME_VALUES: False}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    key = next(k for k in result["data_schema"].schema if str(k) == CONF_POLL_PRIMING)
    assert key.default() == POLL_PRIMING_OFF
    assert CONF_PRIME_VALUES not in {str(k) for k in result["data_schema"].schema}
