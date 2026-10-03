"""Heat pump fault history: decoding ebusd fault entries and the fault code table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

UNKNOWN_FAULT = "Unknown fault"

# aroTHERM plus fault codes (appliance interface manual 0020291573_01, appendix F)
FAULT_CODES: dict[int, str] = {
    22: "Building circuit: water pressure too low",
    42: "Fault: coding resistor",
    73: "Sensor fault: building circuit water pressure",
    94: "Fault: vortex (volume flow sensor)",
    103: "Fault: spare part identification (incorrect control PCB)",
    514: "Sensor fault: compressor inlet temperature",
    517: "Sensor fault: compressor outlet temperature",
    519: "Sensor fault: building circuit return temperature",
    520: "Sensor fault: building circuit flow temperature",
    526: "Sensor fault: EEV outlet temperature",
    546: "Sensor fault: high pressure",
    582: "Fault: EEV",
    585: "Sensor fault: capacitor outlet temperature",
    703: "Sensor fault: low pressure",
    718: "Fan unit 1: fan blocked",
    723: "Building circuit: pressure too low",
    729: "Compressor outlet temperature too low",
    731: "High-pressure switch open",
    732: "Compressor outlet temperature too high",
    733: "Evaporation temperature too low",
    734: "Condensation temperature too low",
    735: "Evaporation temperature too high",
    737: "Condensation temperature too high",
    741: "Building circuit: return temperature too low",
    752: "Fault: frequency converter",
    753: "Connection fault: frequency converter not recognised",
    755: "Fault: 4-port valve position not correct",
    774: "Sensor fault: air inlet temperature",
    785: "Fan unit 2: fan blocked",
    788: "Building circuit: pump fault",
    817: "Frequency converter fault: compressor",
    818: "Frequency converter fault: mains voltage",
    819: "Frequency converter fault: overheating",
    820: "Connection fault: building circuit pump",
    823: "Hot gas temperature switch open",
    825: "Sensor fault: capacitor inlet temperature",
    1117: "Compressor: phase failure",
    9998: "Connection fault: heat pump (eBUS)",
}

STATUS_STORED = 2


def format_code(code: int) -> str:
    """The code as the heat pump display shows it: 22 -> F.022."""
    return f"F.{code:03d}"


def fault_meaning(code: int) -> str:
    return FAULT_CODES.get(code, UNKNOWN_FAULT)


@dataclass(frozen=True)
class FaultEntry:
    code: int
    timestamp: datetime
    status: int

    @property
    def label(self) -> str:
        return format_code(self.code)

    @property
    def meaning(self) -> str:
        return fault_meaning(self.code)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.label,
            "timestamp": self.timestamp.isoformat(),
            "meaning": self.meaning,
        }


def _field(payload: dict, name: str) -> Any:
    value = payload.get(name)
    if isinstance(value, dict):
        value = value.get("value")
    return value


def parse_fault_entry(payload: Any) -> FaultEntry | None:
    """Decode a LastError / FaultHistoryN payload; None for empty or undecodable slots.

    Empty slots have status 1 and the date 00.00.00, and some fail to decode at all
    (ebusd then publishes no fields, or an error string).
    """
    if not isinstance(payload, dict):
        return None
    try:
        status = int(_field(payload, "status"))
        code = int(_field(payload, "error"))
        stamp = datetime.strptime(
            f"{_field(payload, 'date')} {_field(payload, 'time')}", "%d.%m.%Y %H:%M"
        )
    except TypeError, ValueError:
        return None
    if status != STATUS_STORED or code <= 0:
        return None
    return FaultEntry(code, stamp.replace(tzinfo=dt_util.get_default_time_zone()), status)
