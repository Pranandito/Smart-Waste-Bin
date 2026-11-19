"""Pembungkus tunggal koneksi MQTT (satu proses = satu koneksi).

Modul/service lain tidak menyentuh paho langsung: mereka mendaftarkan Last Will
dan hook on_connect/on_message, lalu memanggil publish()/subscribe().
"""
from __future__ import annotations

import logging
from typing import Callable, List

import paho.mqtt.client as mqtt

from .config import Settings

log = logging.getLogger(__name__)


class MqttClient:
    def __init__(self, settings: Settings) -> None:
        self._host = settings.mqtt_host
        self._port = settings.mqtt_port
        self._keepalive = settings.mqtt_keepalive
        self._on_connect_hooks: List[Callable[[], None]] = []
        self._on_message_hooks: List[Callable[[str, str], None]] = []

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.device_id)
        c.reconnect_delay_set(min_delay=1, max_delay=30)
        c.on_connect = self._handle_connect
        c.on_disconnect = self._handle_disconnect
        c.on_message = self._handle_message
        self._client = c

    # --- pendaftaran (panggil sebelum start) -------------------------------
    def set_will(self, topic: str, payload: str, qos: int = 1, retain: bool = True) -> None:
        self._client.will_set(topic, payload, qos=qos, retain=retain)

    def add_on_connect(self, hook: Callable[[], None]) -> None:
        """Hook dipanggil setiap (re)connect berhasil. Cocok untuk publish awal/subscribe."""
        self._on_connect_hooks.append(hook)

    def add_on_message(self, hook: Callable[[str, str], None]) -> None:
        """Tambah handler pesan masuk.

        Signature hook: ``hook(topic: str, payload: str) -> None``
        Hook dipanggil di thread network loop paho — jangan lakukan operasi
        I/O blocking panjang di dalamnya.
        """
        self._on_message_hooks.append(hook)

    # --- operasi ------------------------------------------------------------
    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        return self._client.publish(topic, payload, qos=qos, retain=retain)

    def subscribe(self, topic: str, qos: int = 1) -> None:
        """Subscribe ke topik. Panggil dari hook on_connect agar re-subscribe otomatis."""
        self._client.subscribe(topic, qos=qos)
        log.debug("subscribe → %s (qos=%d)", topic, qos)

    def start(self) -> None:
        """Non-blocking: connect di background, retry otomatis jika broker belum siap."""
        self._client.connect_async(self._host, self._port, self._keepalive)
        self._client.loop_start()

    def stop(self) -> None:
        # Sengaja TANPA disconnect(): DISCONNECT normal membuat broker membuang
        # Last Will. Dengan hanya menghentikan loop, socket tertutup saat proses
        # keluar dan broker mempublish LWT "offline".
        self._client.loop_stop()

    # --- callback paho (jalan di thread network loop) -------------------------
    def _handle_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("Koneksi ditolak broker: %s", reason_code)
            return
        log.info("Terhubung ke broker %s:%s", self._host, self._port)
        for hook in self._on_connect_hooks:
            try:
                hook()
            except Exception:  # jangan sampai thread network loop mati
                log.exception("on_connect hook gagal")

    def _handle_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("Koneksi terputus: %s (reconnect otomatis)", reason_code)
        else:
            log.info("Koneksi ditutup")

    def _handle_message(self, client, userdata, message) -> None:
        try:
            payload = message.payload.decode()
        except UnicodeDecodeError:
            log.warning("Pesan non-UTF-8 diabaikan pada topik %s", message.topic)
            return
        for hook in self._on_message_hooks:
            try:
                hook(message.topic, payload)
            except Exception:  # jangan sampai thread network loop mati
                log.exception("on_message hook gagal (topic=%s)", message.topic)
