"""Noise reduction schedule: the controller's SilentTimer_<Day> messages."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# SilentTimer_Monday (the slot is in a slotindex field) or SilentTimer_Monday0 (slot in the name)
_NAME = re.compile(r"^SilentTimer_([A-Za-z]+?)(\d*)$")


def _value(payload: dict, name: str) -> Any:
    value = payload.get(name)
    return value.get("value") if isinstance(value, dict) else value


def _minutes(text: Any) -> int | None:
    """ "08:30" -> 510; "24:00" -> 1440."""
    try:
        hours, minutes = str(text).split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


class NoiseSchedule:
    """Weekly noise reduction periods, filled from whatever slots ebusd has published."""

    def __init__(self) -> None:
        # weekday index -> slot -> (start minutes, end minutes)
        self._slots: dict[int, dict[int, tuple[int, int]]] = {}
        # weekday index -> slot indexes seen (empty slots too) and the day's slot count
        self._seen: dict[int, set[int]] = {}
        self._count: dict[int, int] = {}

    @property
    def known(self) -> bool:
        return bool(self._slots)

    def update(self, message: str, payload: Any) -> None:
        """Take over one SilentTimer message; unusable payloads are ignored."""
        if not isinstance(payload, dict) or not (match := _NAME.match(message)):
            return
        day = next((i for i, d in enumerate(_DAYS) if d.lower() == match.group(1).lower()), None)
        if day is None:
            return
        slot = match.group(2)
        try:
            index = int(slot) if slot else int(_value(payload, "slotindex") or 0)
        except (TypeError, ValueError):  # fmt: skip
            return
        start, end = _minutes(_value(payload, "htm")), _minutes(_value(payload, "htm_1"))
        slots = self._slots.setdefault(day, {})
        self._seen.setdefault(day, set()).add(index)
        try:
            self._count[day] = int(_value(payload, "slotcount"))
        except (TypeError, ValueError):  # fmt: skip
            pass
        if start is None or end is None or start == end:
            slots.pop(index, None)  # an empty slot (00:00 - 00:00)
        else:
            slots[index] = (start, end)

    def active_at(self, now: datetime) -> bool | None:
        """Whether a period of today's schedule covers *now*; None while nothing is known."""
        if not self._slots:
            return None
        day = now.weekday()
        # only some of the day's slots are known: do not claim "off"
        if len(self._seen.get(day, ())) < self._count.get(day, 0):
            return None
        minute = now.hour * 60 + now.minute
        return any(start <= minute < end for start, end in self._slots.get(day, {}).values())

    def as_dict(self) -> dict[str, list[str]]:
        return {
            _DAYS[day]: [
                f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}"
                for s, e in sorted(slots.values())
            ]
            for day, slots in sorted(self._slots.items())
        }
