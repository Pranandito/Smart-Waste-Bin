# waste-bin

Agen MQTT untuk Orange Pi. Menangani koneksi ke broker, kanal `status` (online + Last Will), kanal `info` (keadaan perangkat), kanal `config` (konfigurasi remote persisten), dan kanal `telemetry` (pembacaan berkala 3 sensor ultrasonik A02YYUW).

## Struktur

```
waste-bin/
├── __main__.py          # entry point: susun komponen, jalankan, tangani SIGINT/SIGTERM
├── config.py            # Settings dari environment variable & DeviceState persisten
├── clock.py             # waktu NTP (SNTP) -> ts ISO 8601
├── protocol.py          # format topic + payload
├── mqtt_client.py       # satu koneksi MQTT, LWT, hook on_connect/on_message, pub/sub
├── sensors.py           # pembacaan 3 sensor ultrasonik A02YYUW & kalkulasi kapasitas
└── services/
    ├── status.py        # kanal status (online + Last Will)
    ├── info.py          # kanal info (keadaan perangkat saat connect)
    ├── remote_config.py # kanal config (terima konfigurasi persisten)
    └── telemetry.py     # kanal telemetry (publish berkala data sensor)
deploy/
├── waste-bin.service
└── waste-bin.env.example
```

Topic: `bisma/waste/{zona}/{device_id}/{kanal}`
Payload: `{"device_id":..., "ts":"2026-11-19T02:38:00Z", "fw":"0.1.1", "values":{...}}`

## Development (Windows)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python waste-bin
```

Default konfigurasi sudah `bin-001`, `lantai-9`, `localhost:1883`. Ubah lewat environment variable (lihat `deploy/waste-bin.env.example`).

## Deploy (Orange Pi, systemd)

```bash
sudo mkdir -p /opt/waste-bin && sudo chown orangepi: /opt/waste-bin
# salin isi proyek ke /opt/waste-bin, lalu:
cd /opt/waste-bin
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -r requirements.txt

sudo cp deploy/waste-bin.env.example /etc/waste-bin.env   # sesuaikan
sudo cp deploy/waste-bin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now waste-bin
journalctl -u waste-bin -f
```

Sesuaikan `User=` dan path di file service jika berbeda.

## Menambah fitur nanti

Buat modul baru di `waste-bin/services/` (mis. `telemetry.py`) yang menerima `MqttClient`, `Protocol`, dan dependensi lain, lalu daftarkan di `waste-bin/__main__.py`.
- Publish: `mqtt.publish(protocol.topic("telemetry"), protocol.payload({...}))`
- Subscribe: panggil `subscribe` di hook `add_on_connect` agar otomatis diulang saat reconnect.

## Catatan Last Will

`stop()` sengaja tidak memanggil `disconnect()`, supaya broker memicu LWT `offline` (juga saat `systemctl stop`). Payload LWT memakai `"ts": false`.
