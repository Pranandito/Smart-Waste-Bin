"""Entry point: python waste-bin atau python -m waste_bin"""
from __future__ import annotations

import logging
import signal
import sys
import threading

from .clock import NtpClock
from .config import Settings, DeviceState
from .mqtt_client import MqttClient
from .protocol import Protocol
from .services.info import InfoService
from .services.remote_config import RemoteConfigService
from .services.status import StatusService

log = logging.getLogger("waste_bin")


def main() -> int:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    # Log ke stdout/stderr -> ditangkap journald saat dijalankan systemd
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    clock = NtpClock(settings.ntp_host)
    clock.sync()

    protocol = Protocol(settings, clock)
    mqtt = MqttClient(settings)

    # State persisten perangkat (interval telemetry, dsb.)
    state = DeviceState.load()

    # Daftarkan service di sini. Tiap service memasang Last Will / hook-nya sendiri.
    StatusService(mqtt, protocol)
    InfoService(mqtt, protocol)
    RemoteConfigService(mqtt, protocol, state)

    mqtt.start()
    log.info("waste-bin fw=%s berjalan (device_id=%s)", settings.fw, settings.device_id)

    while not stop.wait(1):  # polling 1 detik agar Ctrl+C responsif di Windows
        pass

    log.info("Berhenti")
    mqtt.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
