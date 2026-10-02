"""Services for automations: heating boost (quick veto), away, hot water boost."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_entity_ids

from .const import DOMAIN

SERVICE_SET_QUICK_VETO = "set_quick_veto"
SERVICE_CANCEL_QUICK_VETO = "cancel_quick_veto"
SERVICE_SET_AWAY = "set_away"
SERVICE_CANCEL_AWAY = "cancel_away"
SERVICE_HOT_WATER_BOOST = "hot_water_boost"
ENTITY_SERVICES = [
    SERVICE_SET_QUICK_VETO,
    SERVICE_CANCEL_QUICK_VETO,
    SERVICE_SET_AWAY,
    SERVICE_CANCEL_AWAY,
    SERVICE_HOT_WATER_BOOST,
]

ATTR_DURATION_HOURS = "duration_hours"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_ENABLE = "enable"

_ENTITIES_KEY = f"{DOMAIN}_entities"

_TARGET = cv.make_entity_service_schema({})
SCHEMAS = {
    SERVICE_SET_QUICK_VETO: cv.make_entity_service_schema(
        {
            vol.Required(ATTR_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(5, 30)),
            vol.Optional(ATTR_DURATION_HOURS): vol.All(vol.Coerce(float), vol.Range(0.5, 24)),
        }
    ),
    SERVICE_CANCEL_QUICK_VETO: _TARGET,
    SERVICE_SET_AWAY: vol.All(
        cv.make_entity_service_schema(
            {
                vol.Required(ATTR_START_DATE): cv.date,
                vol.Required(ATTR_END_DATE): cv.date,
            }
        ),
        lambda data: _check_dates(data),
    ),
    SERVICE_CANCEL_AWAY: _TARGET,
    SERVICE_HOT_WATER_BOOST: cv.make_entity_service_schema(
        {vol.Optional(ATTR_ENABLE, default=True): cv.boolean}
    ),
}

# service -> entity method name; entities without the method are rejected
_METHODS = {
    SERVICE_SET_QUICK_VETO: "async_service_set_quick_veto",
    SERVICE_CANCEL_QUICK_VETO: "async_service_cancel_quick_veto",
    SERVICE_SET_AWAY: "async_service_set_away",
    SERVICE_CANCEL_AWAY: "async_service_cancel_away",
    SERVICE_HOT_WATER_BOOST: "async_service_hot_water_boost",
}


def _check_dates(data: dict[str, Any]) -> dict[str, Any]:
    start: date = data[ATTR_START_DATE]
    end: date = data[ATTR_END_DATE]
    if end < start:
        raise vol.Invalid("end_date must not be before start_date")
    return data


@callback
def register_entity(hass: HomeAssistant, entity: Any) -> None:
    """Make an entity reachable by the integration's services."""
    hass.data.setdefault(_ENTITIES_KEY, set()).add(entity)


@callback
def unregister_entity(hass: HomeAssistant, entity: Any) -> None:
    hass.data.get(_ENTITIES_KEY, set()).discard(entity)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_QUICK_VETO):
        return

    async def _handle(call: ServiceCall) -> None:
        method = _METHODS[call.service]
        # by the current entity ID, so renamed entities keep working
        entities = {e.entity_id: e for e in hass.data.get(_ENTITIES_KEY, set())}
        targets = []
        for entity_id in sorted(await async_extract_entity_ids(hass, call)):
            entity = entities.get(entity_id)
            if entity is None or not hasattr(entity, method):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unsupported_entity",
                    translation_placeholders={"entity_id": entity_id, "service": call.service},
                )
            targets.append(entity)
        data = {k: v for k, v in call.data.items() if k not in cv.ENTITY_SERVICE_FIELDS}
        for entity in targets:
            await getattr(entity, method)(**data)

    for service in ENTITY_SERVICES:
        hass.services.async_register(DOMAIN, service, _handle, schema=SCHEMAS[service])


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    for service in ENTITY_SERVICES:
        hass.services.async_remove(DOMAIN, service)
    hass.data.pop(_ENTITIES_KEY, None)
