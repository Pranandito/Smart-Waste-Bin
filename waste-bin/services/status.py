"""Kanal status: "online" saat (re)connect, "offline" lewat Last Will broker."""
from __future__ import annotations

from ..mqtt_client import MqttClient
from ..protocol import Protocol

KANAL = "status"


class StatusService:
    def __init__(self, mqtt: MqttClient, protocol: Protocol) -> None:
        self._mqtt = mqtt
        self._protocol = protocol
        self._topic = protocol.topic(KANAL)

        mqtt.set_will(
            self._topic,
            protocol.will_payload({"status": "offline"}),
            qos=1,
            retain=True,
        )
        mqtt.add_on_connect(self._announce_online)

    def _announce_online(self) -> None:
        self._mqtt.publish(
            self._topic,
            self._protocol.payload({"status": "online"}),
            qos=1,
            retain=True,
        )
