"""Kanal info: keadaan perangkat saat (re)connect — QoS 1, retain.

Publish satu kali setiap kali koneksi MQTT berhasil dibentuk sehingga
broker selalu menyimpan salinan terakhir untuk subscriber yang baru join.

Payload (via Protocol.payload):
    {
        "device_id": "...",
        "ts": "2026-11-19T03:00:00Z",
        "fw": "0.1.1",
        "values": {
            "hw_rev": "A",
            "mac": "...", "mac_eth": "...", "link": "eth",
            "ip": "...", "rssi": 0,
            "reset_reason": null, "boot_count": 0,
            "uptime_s": 0, "heap_min": 0,
            "slot": null, "ota_pending": false
        }
    }

Untuk upgrade ke data nyata: edit saja ``DeviceInfo.collect()`` di
``config.py`` — kelas ini tidak perlu disentuh.
"""
from __future__ import annotations

import logging

from ..config import DeviceInfo
from ..mqtt_client import MqttClient
from ..protocol import Protocol

log = logging.getLogger(__name__)

KANAL = "info"


class InfoService:
    """Publish info perangkat ke kanal ``info`` setiap (re)connect."""

    def __init__(self, mqtt: MqttClient, protocol: Protocol) -> None:
        self._mqtt = mqtt
        self._protocol = protocol
        self._topic = protocol.topic(KANAL)
        mqtt.add_on_connect(self._publish_info)

    # ------------------------------------------------------------------
    def _publish_info(self) -> None:
        info = DeviceInfo.collect()
        payload = self._protocol.payload(info.to_dict())
        self._mqtt.publish(self._topic, payload, qos=1, retain=True)
        log.info(
            "info dikirim → %s (mac=%s ip=%s reset=%s)",
            self._topic,
            info.mac,
            info.ip,
            info.reset_reason,
        )
