"""Write protection for setpoints: the controller stores them in EEPROM.

Sliders and number boxes can fire many changes per second. For each write topic:

- the first change is sent right away;
- further changes are coalesced and only the last one is sent, once nothing changed for
  ``QUIET_SECONDS`` and at least ``MIN_INTERVAL_SECONDS`` after the previous write;
- a value equal to the current one is not written (unless the caller says otherwise).

Mode, boost and away writes are user actions, not slider spam, and bypass this guard.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

QUIET_SECONDS = 1.5
MIN_INTERVAL_SECONDS = 10.0
# How long after a write the cached value may still be the old one (ebusd's echo of
# the write has not arrived yet), so "equal to the cache" does not mean "unchanged".
ECHO_WINDOW_SECONDS = 30.0


def _same(current: Any, payload: str) -> bool:
    if current is None:
        return False
    try:
        return abs(float(current) - float(payload)) < 1e-6
    except TypeError, ValueError:
        return str(current) == payload


class WriteGuard:
    """Debounce, rate-limit and de-duplicate setpoint writes per MQTT topic."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._last_sent: dict[str, datetime] = {}
        self._last_payload: dict[str, str] = {}
        self._pending: dict[str, str] = {}
        self._timers: dict[str, Callable[[], None]] = {}

    async def async_write(self, topic: str, payload: str, current: Any = None) -> None:
        """Write *payload* to *topic*; *current* is the value it would replace, if known."""
        now = dt_util.utcnow()
        last = self._last_sent.get(topic)
        if _same(current, payload) and self._settled(topic, payload, now):
            _LOGGER.debug("Write skipped, unchanged: %s = %s", topic, payload)
            self._cancel(topic)
            self._pending.pop(topic, None)
            return
        if topic not in self._pending and (
            last is None or (now - last).total_seconds() >= MIN_INTERVAL_SECONDS
        ):
            await self._send(topic, payload)
            return
        self._pending[topic] = payload
        since_last = (now - last).total_seconds() if last else MIN_INTERVAL_SECONDS
        delay = max(QUIET_SECONDS, MIN_INTERVAL_SECONDS - since_last)
        _LOGGER.debug("Write coalesced: %s = %s (in %.1f s)", topic, payload, delay)
        self._cancel(topic)
        self._timers[topic] = async_call_later(self._hass, delay, self._flush_cb(topic))

    def _settled(self, topic: str, payload: str, now: datetime) -> bool:
        """Whether the cache can be trusted: no recent write of a different value."""
        last = self._last_sent.get(topic)
        if last is None or (now - last).total_seconds() >= ECHO_WINDOW_SECONDS:
            return True
        return _same(self._last_payload.get(topic), payload)

    def _flush_cb(self, topic: str) -> Callable[[Any], None]:
        @callback
        def _flush(_now: Any) -> None:
            self._timers.pop(topic, None)
            payload = self._pending.pop(topic, None)
            if payload is not None:
                self._hass.async_create_task(self._send(topic, payload))

        return _flush

    async def _send(self, topic: str, payload: str) -> None:
        self._last_sent[topic] = dt_util.utcnow()
        self._last_payload[topic] = payload
        _LOGGER.debug("MQTT write: %s -> %s", topic, payload)
        await mqtt.async_publish(self._hass, topic, payload)

    def _cancel(self, topic: str) -> None:
        if (unsub := self._timers.pop(topic, None)) is not None:
            unsub()

    @callback
    def async_stop(self) -> None:
        """Send pending writes now (on unload), so the last value is not lost."""
        for unsub in self._timers.values():
            unsub()
        self._timers.clear()
        for topic, payload in self._pending.items():
            self._hass.async_create_task(self._send(topic, payload))
        self._pending.clear()
