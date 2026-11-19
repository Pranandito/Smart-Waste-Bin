"""Kanal config: terima parameter dari server, simpan persisten.

Subscribe ke topik ``…/config`` dengan QoS 1. Setiap pesan yang masuk
divalidasi, diterapkan ke ``DeviceState``, dan langsung disimpan ke disk
sehingga nilai tetap ada setelah restart.

Format pesan dari server (nilai dalam ``values`` atau di root payload):
    {
        "values": {
            "telemetry_interval_s": 30
        }
    }

Untuk menambah parameter baru:
1. Tambahkan field di ``DeviceState`` (config.py).
2. Tambahkan handler ``_apply_<field>`` di kelas ini.
3. Panggil handler baru di ``_apply_values()``.
"""
from __future__ import annotations

import json
import logging

from ..config import (
    DeviceState,
    TELEMETRY_INTERVAL_MIN,
    TELEMETRY_INTERVAL_MAX,
)
from ..mqtt_client import MqttClient
from ..protocol import Protocol

log = logging.getLogger(__name__)

KANAL = "config"


class RemoteConfigService:
    """Terima dan terapkan konfigurasi dari server via MQTT."""

    def __init__(
        self,
        mqtt: MqttClient,
        protocol: Protocol,
        state: DeviceState,
    ) -> None:
        self._mqtt = mqtt
        self._topic = protocol.topic(KANAL)
        self._state = state

        mqtt.add_on_connect(self._subscribe)
        mqtt.add_on_message(self._on_message)

        log.info(
            "RemoteConfigService siap (telemetry_interval_s=%ds)",
            state.telemetry_interval_s,
        )

    # ------------------------------------------------------------------
    # Properti — dibaca oleh service lain (mis. TelemetryService)
    # ------------------------------------------------------------------

    @property
    def telemetry_interval_s(self) -> int:
        return self._state.telemetry_interval_s

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        self._mqtt.subscribe(self._topic, qos=1)
        log.info("subscribed → %s", self._topic)

    def _on_message(self, topic: str, payload: str) -> None:
        if topic != self._topic:
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            log.warning("config: payload bukan JSON valid — %s", exc)
            return

        if not isinstance(data, dict):
            log.warning("config: payload harus berupa objek JSON")
            return

        # Payload dari server bisa berformat penuh {values:{...}} atau
        # ringkas langsung {telemetry_interval_s: ...}
        values = data.get("values") if isinstance(data.get("values"), dict) else data

        changed = self._apply_values(values)

        if changed:
            self._state.save()

    def _apply_values(self, values: dict) -> bool:
        """Terapkan semua field yang dikenal; kembalikan True jika ada yang berubah."""
        changed = False
        changed |= self._apply_telemetry_interval(values)
        # Tambahkan baris berikutnya untuk parameter baru:
        # changed |= self._apply_report_level(values)
        return changed

    def _apply_telemetry_interval(self, values: dict) -> bool:
        raw = values.get("telemetry_interval_s")
        if raw is None:
            return False

        try:
            new_val = int(raw)
        except (TypeError, ValueError):
            log.warning("config: telemetry_interval_s=%r bukan angka bulat", raw)
            return False

        if not (TELEMETRY_INTERVAL_MIN <= new_val <= TELEMETRY_INTERVAL_MAX):
            log.warning(
                "config: telemetry_interval_s=%d di luar batas [%d, %d], diabaikan",
                new_val,
                TELEMETRY_INTERVAL_MIN,
                TELEMETRY_INTERVAL_MAX,
            )
            return False

        if new_val == self._state.telemetry_interval_s:
            log.debug("config: telemetry_interval_s tidak berubah (%ds)", new_val)
            return False

        old = self._state.telemetry_interval_s
        self._state.telemetry_interval_s = new_val
        log.info("config: telemetry_interval_s %d→%ds", old, new_val)
        return True
