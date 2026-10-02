"""EEPROM write protection: debounce, rate limit and skip unchanged setpoints."""

import json
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.ebusd_vaillant.const import DOMAIN

C = "ebusd/ctlv3"
MSGS = {
    f"{C}/HwcTempDesired": 50,
    f"{C}/HwcStorageTemp": 48,
    f"{C}/HwcOpMode": "auto",
    f"{C}/Z1DayTemp": 20.5,
    f"{C}/Z1RoomTemp": 21.0,
    f"{C}/Z1QuickVetoTemp": 21,
    f"{C}/Z1QuickVetoDuration": 3,
    f"{C}/Z1OpMode": "auto",
    "ebusd/hmu/YieldCooling": 0,
}
HWC_SET = f"{C}/HwcTempDesired/set"


async def _setup(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        for topic, value in MSGS.items():
            async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
        await hass.async_block_till_done()
    return entry


def _writes(mqtt_client_mock, topic: str) -> list[str]:
    out = []
    for c in mqtt_client_mock.publish.call_args_list:
        if c.args[0] == topic:
            payload = c.args[1]
            out.append(payload.decode() if isinstance(payload, bytes) else str(payload))
    return out


async def _set_hwc(hass, temp: float) -> None:
    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": "water_heater.vaillant_hot_water", "temperature": temp},
        blocking=True,
    )


async def _advance(hass, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_slider_burst_sends_first_and_last(hass, mqtt_mock, mqtt_client_mock, freezer):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    for temp in (51, 52, 53, 54, 55):
        await _set_hwc(hass, temp)
        await _advance(hass, freezer, 0.3)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0"]  # first one right away

    await _advance(hass, freezer, 2)  # quiet, but within 10 s of the first write
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0"]
    await _advance(hass, freezer, 10)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0", "55.0"]  # only the final value


async def test_next_change_after_interval_is_immediate(hass, mqtt_mock, mqtt_client_mock, freezer):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)
    await _advance(hass, freezer, 11)
    await _set_hwc(hass, 52)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0", "52.0"]


async def test_unchanged_setpoint_not_written(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 50)
    assert _writes(mqtt_client_mock, HWC_SET) == []


async def test_returning_to_current_value_cancels_pending(
    hass, mqtt_mock, mqtt_client_mock, freezer
):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)
    async_fire_mqtt_message(hass, f"{C}/HwcTempDesired", json.dumps({"value": {"value": 51}}))
    await hass.async_block_till_done()
    await _set_hwc(hass, 53)  # pending
    await _set_hwc(hass, 51)  # back to the current value: nothing to write
    await _advance(hass, freezer, 15)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0"]


async def test_quick_veto_written_even_if_unchanged(hass, mqtt_mock, mqtt_client_mock):
    """Writing the quick veto starts it; equal values must not be skipped."""
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.vaillant_zone_1", "temperature": 21},
        blocking=True,
    )
    assert _writes(mqtt_client_mock, f"{C}/Z1QuickVetoTemp/set") == ["21.0"]


async def test_mode_writes_are_not_throttled(hass, mqtt_mock, mqtt_client_mock):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    for mode in ("off", "auto"):
        await hass.services.async_call(
            "water_heater",
            "set_operation_mode",
            {"entity_id": "water_heater.vaillant_hot_water", "operation_mode": mode},
            blocking=True,
        )
    assert _writes(mqtt_client_mock, f"{C}/HwcOpMode/set") == ["off", "auto"]


async def test_pending_write_flushed_on_unload(hass, mqtt_mock, mqtt_client_mock):
    entry = await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)
    await _set_hwc(hass, 54)  # pending
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0", "54.0"]


async def test_back_to_cached_value_before_echo_is_written(
    hass, mqtt_mock, mqtt_client_mock, freezer
):
    """50 -> 51 -> 50 before ebusd echoes 51: the controller must end at 50, not 51."""
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)  # sent at once; the cache still says 50
    await _set_hwc(hass, 50)
    await _advance(hass, freezer, 15)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0", "50.0"]


async def test_cache_trusted_again_after_echo_window(hass, mqtt_mock, mqtt_client_mock, freezer):
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)
    async_fire_mqtt_message(hass, f"{C}/HwcTempDesired", json.dumps({"value": {"value": 50}}))
    await hass.async_block_till_done()  # changed back on the controller's own panel
    await _advance(hass, freezer, 31)
    await _set_hwc(hass, 50)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0"]


async def test_rejected_write_can_be_retried(hass, mqtt_mock, mqtt_client_mock, freezer):
    """Our own /set message is not a value: if ebusd rejects 51, setting 51 again writes."""
    await _setup(hass)
    mqtt_client_mock.publish.reset_mock()
    await _set_hwc(hass, 51)
    async_fire_mqtt_message(hass, HWC_SET, "51.0")  # the broker echoes our own write
    await hass.async_block_till_done()
    await _advance(hass, freezer, 31)  # ebusd never confirmed it
    await _set_hwc(hass, 51)
    assert _writes(mqtt_client_mock, HWC_SET) == ["51.0", "51.0"]
