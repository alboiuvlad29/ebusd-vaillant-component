"""Diagnostics download and the old-definitions Repairs hint."""

import json

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.ebusd_vaillant.const import CONF_COOLING, DOMAIN
from custom_components.ebusd_vaillant.diagnostics import async_get_config_entry_diagnostics

C = "ebusd/ctlv3"
OLD = {f"{C}/Z1DayTemp": 20.5, f"{C}/Z1RoomTemp": 21.0, f"{C}/Z1OpMode": "day"}
NEW = {f"{C}/Z1ManualTemp": 20.5, f"{C}/Z1RoomTemp": 21.0, f"{C}/Z1OpMode": "manual"}


async def _setup(hass, msgs: dict, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"mqtt_prefix": "ebusd"}, options=options or {})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _send(hass, msgs)
    return entry


async def _send(hass, msgs: dict) -> None:
    for topic, value in msgs.items():
        async_fire_mqtt_message(hass, topic, json.dumps({"value": {"value": value}}))
    await hass.async_block_till_done()


async def test_diagnostics(hass, mqtt_mock):
    entry = await _setup(hass, NEW, {CONF_COOLING: "disabled"})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["versions"]["integration"]
    assert diag["entry"]["options"] == {CONF_COOLING: "disabled"}
    assert diag["mqtt_values"]["ctlv3"]["Z1OpMode"] == {"value": {"value": "manual"}}
    zone = next(e for e in diag["discovered_entities"] if e["type"] == "DiscoveredClimate")
    assert zone["mode_vocab"] == "manual"
    assert zone["target_temperature"]["read_topic"] == f"{C}/Z1ManualTemp"
    json.dumps(diag)  # must be serialisable


async def test_old_definitions_hint(hass, mqtt_mock):
    await _setup(hass, OLD)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "old_definitions_ebusd")
    assert issue is not None
    assert issue.translation_placeholders["zones"] == "Vaillant Zone 1"
    assert issue.learn_more_url.startswith("https://github.com/john30/ebusd-configuration")


async def test_no_hint_with_new_definitions(hass, mqtt_mock):
    await _setup(hass, NEW)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "old_definitions_ebusd") is None


async def test_hint_clears_after_switching(hass, mqtt_mock):
    await _setup(hass, OLD)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "old_definitions_ebusd") is not None
    await _send(hass, {f"{C}/Z1ManualTemp": 20.5, f"{C}/Z1OpMode": "manual"})
    assert ir.async_get(hass).async_get_issue(DOMAIN, "old_definitions_ebusd") is None
