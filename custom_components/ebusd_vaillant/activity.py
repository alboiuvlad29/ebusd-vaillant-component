"""What the heat pump is doing right now, from the fastest signals ebusd overhears.

Order of trust (fresh first, polled last):

1. ``Status00.defrost`` (controller reads it every 60 s)
2. hot water: ``Status07.heatermain_b7_warmwater`` (every ~4 s) or ``Status01.pumpstate = hwc``
3. ``Status07.power`` (compressor %, every ~4 s)
4. ``Status00.compressorstate``
5. ``Status01.pumpstate`` (``off``/``on``/``overrun``/``hwc``, every 10 s)
6. ``RunDataStatuscode`` (polled; values such as ``heat_compressor_shutdown`` linger)
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .const import _STAT_HVAC_ACTION_COOLING, _STAT_HVAC_ACTION_HEATING

ACTIVITY_HEATING = "heating"
ACTIVITY_COOLING = "cooling"
ACTIVITY_HOT_WATER = "hot_water"
ACTIVITY_DEFROST = "defrost"
ACTIVITY_IDLE = "idle"
ACTIVITIES = [
    ACTIVITY_HEATING,
    ACTIVITY_COOLING,
    ACTIVITY_HOT_WATER,
    ACTIVITY_DEFROST,
    ACTIVITY_IDLE,
]

# (message, field) per signal; the first device that has the message wins.
ACTIVITY_SOURCES: dict[str, tuple[str, str]] = {
    "defrost": ("Status00", "defrost"),
    "compressor": ("Status00", "compressorstate"),
    "pumpstate": ("Status01", "pumpstate"),
    "warmwater": ("Status07", "heatermain_b7_warmwater"),
    "heating_bit": ("Status07", "heatermain_b3_heating"),
    "power": ("Status07", "power"),
}

_ON = frozenset({"1", "on", "yes", "true"})
_OFF = frozenset({"0", "off", "no", "false"})


def _flag(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _ON:
        return True
    if text in _OFF:
        return False
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


@dataclass
class ActivityTopics:
    """Read topics for the activity signals that exist on this bus."""

    defrost: Any = None
    compressor: Any = None
    pumpstate: Any = None
    warmwater: Any = None
    heating_bit: Any = None
    power: Any = None
    statuscode: Any = None

    def items(self) -> list[tuple[str, Any]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def present(self) -> tuple[str, ...]:
        return tuple(name for name, cfg in self.items() if cfg is not None)


def compute_activity(values: dict[str, Any]) -> str | None:
    """Return one of ACTIVITIES, or None when nothing is known yet."""
    status = values.get("statuscode")
    status = str(status) if status is not None else None
    cooling = status is not None and (
        status in _STAT_HVAC_ACTION_COOLING or status.startswith("cool_")
    )
    running = ACTIVITY_COOLING if cooling else ACTIVITY_HEATING

    if _flag(values.get("defrost")):
        return ACTIVITY_DEFROST
    pumpstate = values.get("pumpstate")
    pumpstate = str(pumpstate).strip().lower() if pumpstate is not None else None
    if _flag(values.get("warmwater")) or pumpstate == "hwc":
        return ACTIVITY_HOT_WATER

    power = _number(values.get("power"))
    if power is not None:
        return running if power > 0 else ACTIVITY_IDLE
    if _flag(values.get("heating_bit")):
        return ACTIVITY_HEATING
    compressor = _flag(values.get("compressor"))
    if compressor is not None:
        return running if compressor else ACTIVITY_IDLE
    if pumpstate in ("on", "overrun"):
        return running
    if pumpstate == "off":
        return ACTIVITY_IDLE

    if status is None or status == "":
        return None
    if status in _STAT_HVAC_ACTION_HEATING:
        return ACTIVITY_HEATING
    if cooling and status in _STAT_HVAC_ACTION_COOLING:
        return ACTIVITY_COOLING
    if status.startswith("hwc") or "warm" in status or "hot_water" in status:
        return ACTIVITY_HOT_WATER
    if "defrost" in status:
        return ACTIVITY_DEFROST
    return ACTIVITY_IDLE
