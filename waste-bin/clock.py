"""Waktu berbasis NTP (SNTP), tanpa dependensi.

Orange Pi umumnya tidak punya RTC, jadi jam sistem saat boot bisa salah.
Waktu NTP disimpan sebagai jangkar (epoch NTP + monotonic clock), sehingga
hasilnya tidak ikut bergeser jika jam sistem dikoreksi belakangan oleh
chrony/systemd-timesyncd.
"""
from __future__ import annotations

import logging
import socket
import struct
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_NTP_TO_UNIX = 2208988800  # detik antara 1900-01-01 dan 1970-01-01


class NtpClock:
    def __init__(self, host: str, timeout: float = 2.0, retries: int = 3) -> None:
        self._host = host
        self._timeout = timeout
        self._retries = retries
        self._anchor: Optional[Tuple[float, float]] = None  # (epoch NTP, monotonic)

    @property
    def synced(self) -> bool:
        return self._anchor is not None

    def sync(self) -> bool:
        """Sinkron ke NTP. Jika gagal, now() memakai jam sistem."""
        for attempt in range(1, self._retries + 1):
            try:
                epoch = self._query()
            except OSError as exc:
                log.warning("NTP %s gagal (%d/%d): %s", self._host, attempt, self._retries, exc)
                continue
            self._anchor = (float(epoch), time.monotonic())
            log.info("NTP tersinkron: %s", self.iso())
            return True
        log.warning("NTP tidak tersedia, memakai jam sistem")
        return False

    def now(self) -> float:
        if self._anchor is None:
            return time.time()
        epoch, mono = self._anchor
        return epoch + (time.monotonic() - mono)

    def iso(self) -> str:
        """Waktu UTC format ISO 8601, mis. 2026-11-19T02:38:00Z."""
        return datetime.fromtimestamp(self.now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _query(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self._timeout)
            s.sendto(b"\x1b" + 47 * b"\0", (self._host, 123))
            data, _ = s.recvfrom(48)
        if len(data) < 48:
            raise OSError("respons NTP tidak lengkap")
        return struct.unpack("!I", data[40:44])[0] - _NTP_TO_UNIX
