"""Konfigurasi dari environment variable (cocok dengan EnvironmentFile systemd)."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import struct
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional

from . import __version__

log = logging.getLogger(__name__)

_TOPIC_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _topic_safe(name: str, value: str) -> str:
    if not _TOPIC_SAFE.match(value):
        raise ValueError(f"{name} tidak valid untuk topic MQTT: {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    device_id: str = "bin-001"
    zona: str = "lantai-9"
    fw: str = __version__
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_keepalive: int = 30
    ntp_host: str = "id.pool.ntp.org"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        env = os.environ.get
        try:
            return cls(
                device_id=_topic_safe("DEVICE_ID", env("DEVICE_ID", d.device_id)),
                zona=_topic_safe("ZONA", env("ZONA", d.zona)),
                mqtt_host=env("MQTT_HOST", d.mqtt_host),
                mqtt_port=int(env("MQTT_PORT", d.mqtt_port)),
                mqtt_keepalive=int(env("MQTT_KEEPALIVE", d.mqtt_keepalive)),
                ntp_host=env("NTP_HOST", d.ntp_host),
                log_level=env("LOG_LEVEL", d.log_level).upper(),
            )
        except ValueError as exc:
            raise ValueError(f"Konfigurasi tidak valid: {exc}") from None


# ---------------------------------------------------------------------------
# Informasi hardware / runtime perangkat
# ---------------------------------------------------------------------------
# Semua nilai di sini adalah *placeholder* yang menggambarkan antarmuka akhir.
# Saat sistem asli siap, ganti implementasi _collect_* di bawah ini; semua
# konsumen (InfoService, dsb.) tidak perlu diubah sama sekali.

# --- Konstanta konfigurasi pengumpul data -----------------------------------

_ETH_IFACE  = "eth0"   # nama antarmuka Ethernet
_WIFI_IFACE = "wlan0"  # nama antarmuka Wi-Fi

# File counter boot persisten; buat direktori bila belum ada.
# Ganti ke /var/lib/waste-bin/boot_count.json di deployment produksi.
_STATE_DIR = Path(os.environ.get("WASTE_BIN_STATE_DIR", os.environ.get("BISMA_STATE_DIR", "/var/lib/waste-bin")))
_BOOT_COUNT_FILE = _STATE_DIR / "boot_count.json"

# File sentinel OTA; ditulis oleh update-agent saat ada firmware baru.
_OTA_PENDING_FILE = _STATE_DIR / "ota_pending"

# Symlink yang menunjuk slot OTA aktif ("A" atau "B").
_OTA_SLOT_LINK = _STATE_DIR / "active_slot"

# --- Helper pengumpul data (private) ----------------------------------------

def _read_text(path: str | Path) -> str:
    """Baca file teks; kembalikan string kosong jika gagal."""
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _iface_operstate(iface: str) -> str:
    """Status operasional antarmuka: 'up' | 'down' | 'unknown'."""
    return _read_text(f"/sys/class/net/{iface}/operstate") or "unknown"


def _iface_mac(iface: str) -> str:
    """Alamat MAC antarmuka dalam format xx:xx:xx:xx:xx:xx."""
    mac = _read_text(f"/sys/class/net/{iface}/address")
    return mac if mac else "00:00:00:00:00:00"


def _active_link() -> str:
    """Deteksi antarmuka aktif: 'eth' | 'wifi' | 'none'."""
    if _iface_operstate(_ETH_IFACE) == "up":
        return "eth"
    if _iface_operstate(_WIFI_IFACE) == "up":
        return "wifi"
    return "none"


def _iface_ip(iface: str) -> str:
    """IP address antarmuka jaringan tertentu via ioctl SIOCGIFADDR (Linux).

    Fallback: IP dari default route via UDP connect trick.
    """
    try:
        import fcntl  # hanya tersedia di Linux/Unix
        SIOCGIFADDR = 0x8915
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            packed = fcntl.ioctl(
                s.fileno(),
                SIOCGIFADDR,
                struct.pack("256s", iface[:15].encode()),
            )
        return socket.inet_ntoa(packed[20:24])
    except (ImportError, OSError):
        pass
    # Fallback: IP yang dipakai untuk default route
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"


def _wifi_rssi(iface: str = _WIFI_IFACE) -> int:
    """Level sinyal Wi-Fi dalam dBm dari /proc/net/wireless.

    Mengembalikan 0 jika tidak tersedia (sedang pakai eth atau tidak ada Wi-Fi).
    Format kolom: iface status link level noise ...
    """
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines():
            if iface in line:
                parts = line.split()
                # indeks 3 = level (dBm), bisa trailing dot: "-65."
                return int(float(parts[3].rstrip(".")))
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _uptime_s() -> int:
    """Uptime sistem dalam detik dari /proc/uptime."""
    raw = _read_text("/proc/uptime").split()
    try:
        return int(float(raw[0]))
    except (ValueError, IndexError):
        return 0


def _heap_min_bytes() -> int:
    """MemAvailable dari /proc/meminfo sebagai proxy memori bebas (dalam byte)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024  # kB → byte
    except (OSError, ValueError):
        pass
    return 0


def _boot_count(path: Path = _BOOT_COUNT_FILE) -> int:
    """Baca, increment, dan tulis ulang counter boot persisten ke file JSON.

    Mengembalikan 0 jika direktori tidak bisa ditulis (mis. dev environment).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            count = json.loads(path.read_text()).get("boot_count", 0) + 1
        except (OSError, json.JSONDecodeError, KeyError):
            count = 1
        path.write_text(json.dumps({"boot_count": count}))
        return count
    except OSError as exc:
        log.debug("boot_count tidak tersimpan (%s): %s", path, exc)
        return 0


def _ota_slot() -> Optional[str]:
    """Baca slot OTA aktif ('A' | 'B') dari symlink, atau None jika belum ada."""
    try:
        target = _OTA_SLOT_LINK.resolve()
        name = target.name.upper()
        return name if name in ("A", "B") else None
    except OSError:
        return None


def _ota_pending() -> bool:
    """True jika file marker OTA ada (ditulis oleh update-agent)."""
    return _OTA_PENDING_FILE.exists()


# ---------------------------------------------------------------------------
# State perangkat yang perlu persisten antar restart
# ---------------------------------------------------------------------------

_DEVICE_STATE_FILE = _STATE_DIR / "device_state.json"

# Batas validasi untuk nilai yang diterima dari server
TELEMETRY_INTERVAL_MIN: int = 5        # detik (terlalu cepat → beban jaringan)
TELEMETRY_INTERVAL_MAX: int = 86_400   # detik (1 hari)
TELEMETRY_INTERVAL_DEFAULT: int = 60   # detik


@dataclass
class DeviceState:
    """State perangkat yang perlu persisten antar restart.

    Tambahkan field baru di sini; ``load()`` / ``save()`` otomatis
    memprosesnya tanpa perubahan lain. Konsumen cukup baca/tulis atribut.
    """

    telemetry_interval_s: int = TELEMETRY_INTERVAL_DEFAULT
    # Tambahkan field baru di sini, misal:
    # report_level: str = "normal"

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        """Pastikan nilai dalam batas wajar setelah konstruksi / load."""
        self.telemetry_interval_s = max(
            TELEMETRY_INTERVAL_MIN,
            min(self.telemetry_interval_s, TELEMETRY_INTERVAL_MAX),
        )

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path = _DEVICE_STATE_FILE) -> "DeviceState":
        """Load dari file JSON; gunakan default jika file tidak ada atau corrupt.

        Field yang tidak dikenal (dari versi lama) diabaikan aman.
        Field baru yang belum ada di file akan memakai nilai default.
        """
        try:
            raw = json.loads(path.read_text())
            known = {f.name for f in fields(cls)}
            filtered = {k: v for k, v in raw.items() if k in known}
            return cls(**filtered)
        except FileNotFoundError:
            log.info("DeviceState: file tidak ditemukan, pakai default")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("DeviceState: file corrupt (%s), pakai default", exc)
        return cls()

    def save(self, path: Path = _DEVICE_STATE_FILE) -> None:
        """Simpan ke file JSON secara atomik (write tmp → rename).

        Atomic rename melindungi dari korupsi data saat power loss di tengah
        penulisan.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2))
            tmp.replace(path)  # atomic di POSIX; best-effort di Windows
            log.debug("DeviceState disimpan ke %s", path)
        except OSError as exc:
            log.warning("DeviceState: gagal menyimpan ke %s: %s", path, exc)

@dataclass(frozen=True)
class DeviceInfo:
    """Snapshot keadaan perangkat saat boot / (re)connect.

    Tambahkan field baru di sini + di ``to_dict()`` + di ``collect()``.
    Semua konsumen (InfoService, dsb.) tidak perlu diubah.
    """

    # --- hardware ---
    hw_rev: str = "A"                       # revisi PCB; baca dari GPIO/EEPROM
    mac: str = "00:00:00:00:00:00"          # MAC Wi-Fi utama
    mac_eth: str = "00:00:00:00:00:00"      # MAC Ethernet (jika ada)

    # --- jaringan ---
    link: str = "eth"                       # "eth" | "wifi" | "none"
    ip: str = "0.0.0.0"                     # IP aktif antarmuka aktif
    rssi: int = 0                           # dBm Wi-Fi; 0 jika sedang pakai eth

    # --- runtime ---
    reset_reason: Optional[str] = None      # "software"|"watchdog"|"power_on"|…
    boot_count: int = 0                     # counter persisten di NVS/file
    uptime_s: int = 0                       # detik sejak boot (time.monotonic)
    heap_min: int = 0                       # heap bebas minimum (byte); 0 di Linux
    slot: Optional[str] = None             # slot OTA aktif: "A" | "B" | None
    ota_pending: bool = False               # ada firmware baru menunggu reboot?

    def to_dict(self) -> dict:
        """Serialisasi ke dict siap masuk field ``values`` payload MQTT."""
        return {
            "hw_rev": self.hw_rev,
            "mac": self.mac,
            "mac_eth": self.mac_eth,
            "link": self.link,
            "ip": self.ip,
            "rssi": self.rssi,
            "reset_reason": self.reset_reason,
            "boot_count": self.boot_count,
            "uptime_s": self.uptime_s,
            "heap_min": self.heap_min,
            "slot": self.slot,
            "ota_pending": self.ota_pending,
        }

    @classmethod
    def collect(cls) -> "DeviceInfo":
        """Kumpulkan info perangkat dari sistem secara live.

        Field yang masih stub (hw_rev, reset_reason, slot, ota_pending)
        ditandai di komentar masing-masing. Upgrade cukup di sini.
        """
        link = _active_link()
        iface = _ETH_IFACE if link in ("eth", "none") else _WIFI_IFACE

        return cls(
            hw_rev="A",                         # TODO: baca dari EEPROM/GPIO strap
            mac=_iface_mac(_WIFI_IFACE),
            mac_eth=_iface_mac(_ETH_IFACE),
            link=link,
            ip=_iface_ip(iface),
            rssi=_wifi_rssi() if link == "wifi" else 0,
            reset_reason=None,                  # TODO: baca file sentinel systemd/watchdog
            boot_count=_boot_count(),
            uptime_s=_uptime_s(),
            heap_min=_heap_min_bytes(),
            slot=_ota_slot(),
            ota_pending=_ota_pending(),
        )
