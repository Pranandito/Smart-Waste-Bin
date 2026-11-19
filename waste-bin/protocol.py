"""Aturan topic dan format pesan MQTT.

Topic  : bisma/waste/{zona}/{device_id}/{kanal}
Payload: {"device_id", "ts", "fw", "values": {...}}
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .clock import NtpClock
from .config import Settings


class Protocol:
    def __init__(self, settings: Settings, clock: NtpClock) -> None:
        self._device_id = settings.device_id
        self._fw = settings.fw
        self._base = f"bisma/waste/{settings.zona}/{settings.device_id}"
        self._clock = clock

    def topic(self, kanal: str) -> str:
        return f"{self._base}/{kanal}"

    def payload(self, values: Dict[str, Any]) -> str:
        """Payload dengan ts dari NTP saat ini."""
        return self._encode(values, self._clock.iso())

    def will_payload(self, values: Dict[str, Any]) -> str:
        """Payload Last Will. Ditentukan saat connect, jadi ts tidak bisa dinamis -> false."""
        return self._encode(values, False)

    def _encode(self, values: Dict[str, Any], ts: Any) -> str:
        return json.dumps(
            {"device_id": self._device_id, "ts": ts, "fw": self._fw, "values": values},
            separators=(",", ":"),
        )
