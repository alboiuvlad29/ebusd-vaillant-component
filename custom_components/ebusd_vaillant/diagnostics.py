"""Diagnostics download: cached ebusd messages, discovered entities, versions, options."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import EbusdCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Everything needed to reproduce discovery. The topics carry no credentials."""
    coordinator: EbusdCoordinator = hass.data[DOMAIN][entry.entry_id]
    integration = await async_get_integration(hass, DOMAIN)
    entities = coordinator.discovered_entities()
    return {
        "versions": {"integration": str(integration.version), "home_assistant": HA_VERSION},
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "mqtt_values": coordinator.mqtt_values,
        "discovered_entities": [
            {"type": type(e).__name__, **dataclasses.asdict(e)} for e in entities
        ],
    }
