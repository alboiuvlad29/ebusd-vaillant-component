"""Helper to build DeviceInfo for ebusd Vaillant entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DEFAULT_AREA, DEFAULT_MANUFACTURER, DOMAIN

# HA 2026.10 deprecates DeviceInfo(via_device=identifiers) in favour of the parent's
# device registry ID (via_device_id); older versions only know via_device.
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__

# MQTT prefix -> device registry ID of the system (parent) device, set on setup
PARENT_DEVICE_IDS: dict[str, str] = {}


def build_device_info(config) -> DeviceInfo:
    """Return a DeviceInfo for *config*, grouping it under the correct HA device.

    All sub-devices (zones, circuits, hot water) set ``via_device`` to the
    parent system device (identified by the MQTT prefix alone).  The pressure
    sensor is placed directly on the parent, so its ``device_key == parent_key``.
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, config.device_key)},
        name=config.device_name,
        manufacturer=config.manufacturer or DEFAULT_MANUFACTURER,
        suggested_area=DEFAULT_AREA,
    )
    if config.model:
        info["model"] = config.model
    if config.sw_version:
        info["sw_version"] = config.sw_version
    if config.hw_version:
        info["hw_version"] = config.hw_version
    if config.device_key != config.parent_key:
        parent_id = PARENT_DEVICE_IDS.get(config.parent_key)
        if _SUPPORTS_VIA_DEVICE_ID and parent_id:
            info["via_device_id"] = parent_id
        else:
            info["via_device"] = (DOMAIN, config.parent_key)
    return info


class LegacyObjectIdMixin:
    """Keep the entity ID derived from an entity's original English name.

    Entity names come from translations, so renaming one would also change the
    entity ID of new installs. Entities that were renamed set ``_legacy_object_id``
    to their old name, so automations and dashboards keep matching.
    """

    _legacy_object_id: str

    @property
    def suggested_object_id(self) -> str | None:
        return self._legacy_object_id
