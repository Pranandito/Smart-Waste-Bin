"""Kanal telemetry: pembacaan sensor berkala — QoS 1, retain.

Membaca sensor A02YYUW (jarak & persentase keterisian kompartemen 1–3)
dan mengirimkan hasilnya ke broker MQTT secara berkala sesuai interval
pada DeviceState (dapat diperbarui dinamis dari kanal config).

Payload (via Protocol.payload):
    {
        "device_id": "...",
        "ts": "2026-11-20T03:15:00Z",
        "fw": "0.1.1",
        "values": {
            "distance_mm_1": 450,
            "fill_level_percent_1": 55.6,
            "distance_mm_2": 240,
            "fill_level_percent_2": 100.0,
            "distance_mm_3": 700,
            "fill_level_percent_3": 0.0
        }
    }
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..config import DeviceState
from ..mqtt_client import MqttClient
from ..protocol import Protocol
from ..sensors import DistanceSensorGroup, SensorSnapshot

log = logging.getLogger(__name__)

KANAL = "telemetry"


class TelemetryService:
    """Service pengiriman telemetry berkala berbasis background thread."""

    def __init__(
        self,
        mqtt: MqttClient,
        protocol: Protocol,
        state: DeviceState,
        sensors: DistanceSensorGroup,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._mqtt = mqtt
        self._protocol = protocol
        self._state = state
        self._sensors = sensors
        self._stop = stop_event or threading.Event()
        self._topic = protocol.topic(KANAL)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Mulai worker thread pengiriman telemetry."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="telemetry_worker", daemon=True
        )
        self._thread.start()
        log.info(
            "TelemetryService berjalan di background (interval=%ds)",
            self._state.telemetry_interval_s,
        )

    def publish_now(self) -> Optional[SensorSnapshot]:
        """Lakukan satu siklus pembacaan sensor dan publish ke MQTT."""
        snapshot = self._sensors.read_all()
        values = snapshot.to_values_dict()
        payload = self._protocol.payload(values)

        self._mqtt.publish(self._topic, payload, qos=1, retain=True)

        d1 = values.get("distance_mm_1")
        f1 = values.get("fill_level_percent_1")
        d2 = values.get("distance_mm_2")
        f2 = values.get("fill_level_percent_2")
        d3 = values.get("distance_mm_3")
        f3 = values.get("fill_level_percent_3")

        log.info(
            "telemetry dikirim → %s (S1: %s mm / %s%% | S2: %s mm / %s%% | S3: %s mm / %s%%)",
            self._topic,
            d1 if d1 is not None else "-",
            f"{f1:.1f}" if f1 is not None else "-",
            d2 if d2 is not None else "-",
            f"{f2:.1f}" if f2 is not None else "-",
            d3 if d3 is not None else "-",
            f"{f3:.1f}" if f3 is not None else "-",
        )
        return snapshot

    def _run_loop(self) -> None:
        """Loop utama pengiriman berkala."""
        # Beri jeda sejenak saat boot agar koneksi MQTT dan NTP stabil
        if self._stop.wait(1.0):
            return

        while not self._stop.is_set():
            start_time = time.monotonic()
            try:
                self.publish_now()
            except Exception:
                log.exception("Kesalahan saat menjalankan siklus telemetry")

            # Hitung waktu tidur berdasarkan telemetry_interval_s dinamis
            interval = self._state.telemetry_interval_s
            elapsed = time.monotonic() - start_time
            sleep_time = max(1.0, interval - elapsed)

            if self._stop.wait(sleep_time):
                break

        log.info("TelemetryService dihentikan")
