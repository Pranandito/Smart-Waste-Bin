"""Pembacaan tiga sensor ultrasonik A02YYUW pada UART PCB Orange Pi.

Kompartemen:
  - 1: K1 / S1 (UART2) -> /dev/ttyS1
  - 2: K2 / S2 (UART3) -> /dev/ttyS2
  - 3: K3 / S3 (UART4) -> /dev/ttyS3

Kalibrasi:
  - Jarak <= 250 mm (25 cm) -> Penuh (100.0%)
  - Jarak >= 700 mm (70 cm) -> Kosong (0.0%)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Konfigurasi komunikasi UART
BAUD_RATE = 9600
MIN_VALID_MM = 30
MAX_VALID_MM = 4500
READ_WINDOW_SECONDS = 0.15
SENSOR_READ_DELAY = 0.3

# Ambang batas kalibrasi tong (dalam mm)
DISTANCE_FULL_MM = int(os.environ.get("DISTANCE_FULL_MM", 250))    # 25 cm -> 100%
DISTANCE_EMPTY_MM = int(os.environ.get("DISTANCE_EMPTY_MM", 700))  # 70 cm -> 0%

# Port default per kompartemen (bisa di-override lewat env)
DEFAULT_PORTS: Dict[int, str] = {
    1: os.environ.get("SENSOR_PORT_1", "/dev/ttyS1"),
    2: os.environ.get("SENSOR_PORT_2", "/dev/ttyS2"),
    3: os.environ.get("SENSOR_PORT_3", "/dev/ttyS3"),
}


def calculate_fill_percent(
    distance_mm: Optional[int],
    full_mm: int = DISTANCE_FULL_MM,
    empty_mm: int = DISTANCE_EMPTY_MM,
) -> Optional[float]:
    """Hitung persentase keterisian tong berdasarkan pembacaan jarak (mm).

    - <= full_mm (25 cm / 250 mm)  -> 100.0%
    - >= empty_mm (70 cm / 700 mm) -> 0.0%
    - Di antara keduanya dihitung secara linier.
    - None jika pembacaan gagal/tidak valid.
    """
    if distance_mm is None:
        return None
    if distance_mm <= full_mm:
        return 100.0
    if distance_mm >= empty_mm:
        return 0.0

    total_effective = empty_mm - full_mm
    if total_effective <= 0:
        return 0.0

    fill = (empty_mm - distance_mm) / total_effective * 100.0
    return round(max(0.0, min(fill, 100.0)), 1)


@dataclass(frozen=True)
class CompartmentReading:
    """Hasil bacaan sensor satu kompartemen."""

    compartment_id: int
    distance_mm: Optional[int]
    fill_level_percent: Optional[float]
    is_valid: bool
    raw_hex: Optional[str] = None

    @property
    def is_full(self) -> bool:
        """Memudahkan fitur event deteksi tong penuh di masa depan."""
        return self.fill_level_percent is not None and self.fill_level_percent >= 100.0


@dataclass(frozen=True)
class SensorSnapshot:
    """Snapshot data pembacaan seluruh kompartemen."""

    readings: Dict[int, CompartmentReading]
    timestamp: float

    def to_values_dict(self) -> Dict[str, Any]:
        """Format field values untuk payload MQTT kanal telemetry."""
        result: Dict[str, Any] = {}
        for comp_id in (1, 2, 3):
            r = self.readings.get(comp_id)
            result[f"distance_mm_{comp_id}"] = r.distance_mm if r else None
            result[f"fill_level_percent_{comp_id}"] = (
                r.fill_level_percent if r else None
            )
        return result


class DistanceSensorGroup:
    """Pengelola pembacaan grup sensor jarak A02YYUW."""

    def __init__(
        self,
        ports: Optional[Dict[int, str]] = None,
        simulate: Optional[bool] = None,
    ) -> None:
        self._port_names = ports or DEFAULT_PORTS.copy()
        self._serial_ports: Dict[int, Any] = {}
        self._lock = threading.Lock()
        self._latest_snapshot: Optional[SensorSnapshot] = None
        self._listeners: List[Callable[[SensorSnapshot], None]] = []

        # Mode simulasi jika diatur via env atau bila di OS non-Linux tanpa pyserial
        sim_env = os.environ.get("SIMULATE_SENSORS", "").lower() in ("1", "true", "yes")
        self._simulate = simulate if simulate is not None else sim_env

        if not self._simulate:
            self._init_serial_ports()

    def _init_serial_ports(self) -> None:
        """Buka koneksi port serial untuk setiap sensor."""
        try:
            import serial
        except ImportError:
            log.warning("Pyserial tidak terpasang, beralih ke pembacaan fallback/simulasi")
            self._simulate = True
            return

        for comp_id, device in self._port_names.items():
            try:
                port = serial.Serial(
                    device,
                    BAUD_RATE,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.02,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
                self._serial_ports[comp_id] = port
                log.info("Sensor K%d UART siap pada %s", comp_id, device)
            except (serial.SerialException, OSError) as exc:
                log.warning("Gagal membuka UART sensor K%d (%s): %s", comp_id, device, exc)
                self._serial_ports[comp_id] = None

    def _baca_frame(
        self, port: Any, batas_waktu: float
    ) -> Tuple[Optional[Tuple[int, bytes]], int]:
        """Baca satu frame A02YYUW valid dan hitung checksum yang gagal."""
        frame = bytearray()
        checksum_gagal = 0

        while time.monotonic() < batas_waktu:
            data = port.read(1)
            if not data:
                continue

            nilai = data[0]
            if not frame:
                if nilai == 0xFF:
                    frame.append(nilai)
                continue

            frame.append(nilai)
            if len(frame) == 4:
                tinggi, rendah, checksum = frame[1:]
                checksum_hitung = (0xFF + tinggi + rendah) & 0xFF

                if checksum == checksum_hitung:
                    jarak_mm = tinggi * 256 + rendah
                    return (jarak_mm, bytes(frame)), checksum_gagal

                checksum_gagal += 1
                # Byte terakhir mungkin merupakan awal frame berikutnya.
                frame = bytearray([nilai]) if nilai == 0xFF else bytearray()

        return None, checksum_gagal

    def read_compartment(self, comp_id: int) -> CompartmentReading:
        """Baca satu kompartemen secara aman."""
        if self._simulate:
            # Nilai dummy realistis untuk simulasi testing
            dummy_distances = {1: 450, 2: 240, 3: 710}
            d = dummy_distances.get(comp_id, 500)
            fill = calculate_fill_percent(d)
            return CompartmentReading(
                compartment_id=comp_id,
                distance_mm=d,
                fill_level_percent=fill,
                is_valid=True,
                raw_hex="FF 01 02 02",
            )

        port = self._serial_ports.get(comp_id)
        if port is None:
            return CompartmentReading(
                compartment_id=comp_id,
                distance_mm=None,
                fill_level_percent=None,
                is_valid=False,
            )

        try:
            port.reset_input_buffer()
            hasil, checksum_gagal = self._baca_frame(
                port, time.monotonic() + READ_WINDOW_SECONDS
            )

            if hasil is None:
                log.debug("Sensor K%d: belum ada frame valid (checksum gagal: %d)", comp_id, checksum_gagal)
                return CompartmentReading(
                    compartment_id=comp_id,
                    distance_mm=None,
                    fill_level_percent=None,
                    is_valid=False,
                )

            jarak_mm, raw_frame = hasil
            raw_hex = " ".join(f"{b:02X}" for b in raw_frame)

            if MIN_VALID_MM <= jarak_mm <= MAX_VALID_MM:
                fill = calculate_fill_percent(jarak_mm)
                return CompartmentReading(
                    compartment_id=comp_id,
                    distance_mm=jarak_mm,
                    fill_level_percent=fill,
                    is_valid=True,
                    raw_hex=raw_hex,
                )
            else:
                log.warning("Sensor K%d: jarak %d mm di luar rentang valid [%d, %d]", comp_id, jarak_mm, MIN_VALID_MM, MAX_VALID_MM)
                return CompartmentReading(
                    compartment_id=comp_id,
                    distance_mm=jarak_mm,
                    fill_level_percent=None,
                    is_valid=False,
                    raw_hex=raw_hex,
                )

        except Exception as exc:
            log.warning("Sensor K%d error saat membaca: %s", comp_id, exc)
            return CompartmentReading(
                compartment_id=comp_id,
                distance_mm=None,
                fill_level_percent=None,
                is_valid=False,
            )

    def read_all(self) -> SensorSnapshot:
        """Baca seluruh kompartemen bergantian secara thread-safe."""
        with self._lock:
            readings: Dict[int, CompartmentReading] = {}
            for comp_id in (1, 2, 3):
                readings[comp_id] = self.read_compartment(comp_id)
                time.sleep(SENSOR_READ_DELAY)

            snapshot = SensorSnapshot(readings=readings, timestamp=time.monotonic())
            self._latest_snapshot = snapshot

            # Beritahu listener jika ada (misal untuk deteksi tong penuh)
            for listener in self._listeners:
                try:
                    listener(snapshot)
                except Exception:
                    log.exception("Sensor listener gagal dipanggil")

            return snapshot

    def get_latest(self) -> Optional[SensorSnapshot]:
        """Akses data pembacaan terakhir tanpa memblokir."""
        return self._latest_snapshot

    def add_listener(self, callback: Callable[[SensorSnapshot], None]) -> None:
        """Daftarkan observer untuk menerima snapshot setiap kali ada bacaan baru."""
        self._listeners.append(callback)

    def close(self) -> None:
        """Tutup semua serial port yang terbuka."""
        with self._lock:
            for comp_id, port in self._serial_ports.items():
                if port is not None:
                    try:
                        if port.is_open:
                            port.close()
                            log.info("Port sensor K%d ditutup", comp_id)
                    except Exception as exc:
                        log.debug("Gagal menutup port sensor K%d: %s", comp_id, exc)
            self._serial_ports.clear()
