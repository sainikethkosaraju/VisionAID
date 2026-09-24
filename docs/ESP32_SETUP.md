# ESP32-S3 Camera Firmware

> **Status:** compiles for `esp32s3` with ESP-IDF v5.4.2 (0 warnings, 1.1 MB image). **Not yet flashed or run on a DFR1154.** The person detector is **PENDING INTEGRATION**; until then the device reports the `detector_unavailable` fault and the backend shows it as DEGRADED — it never claims to be protecting the room.

## Layout

```
edge/esp32/
  components/vision_buffer/   ring buffer (portable C, host-tested)
  components/fall_trigger/    temporal fall trigger (portable C, host-tested)
  firmware/                   ESP-IDF application
    main/app_main.c           tasks: capture (core 1), detect (core 1), uplink (core 0)
    main/camera.c             OV3660 init with verified DFR1154 pin map
    main/uplink.c             Wi-Fi backoff, SNTP, heartbeat, fall events, evidence upload
    main/provisioning.c       NVS config (no secrets in firmware)
    main/detector.c           PENDING: ESP-DL pedestrian_detect
    partitions.csv            16 MB: nvs, 2× OTA (6 MB), models (3.9 MB)
    sdkconfig.defaults        octal PSRAM, TLS CA bundle, task watchdog → reboot
  tests/host/                 make → ASan/UBSan unit tests
  tools/buffer_budget.py      memory planner
  tools/provision.py          per-device NVS image generator
```

## Camera pin map (verified: DFRobot official example)

| Signal | GPIO | Signal | GPIO |
|---|---|---|---|
| XCLK | 5 | SIOD (SDA) | 8 |
| SIOC (SCL) | 9 | VSYNC | 1 |
| HREF | 2 | PCLK | 15 |
| Y9..Y2 | 4, 6, 7, 14, 17, 21, 18, 16 | PWDN / RESET | not connected (-1) |
| IR LED | 47 | SD CS | 10 |

## Build & flash

```bash
# ESP-IDF v5.4.x
. $IDF_PATH/export.sh
cd edge/esp32/firmware
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

## Provision a device

1. Register the camera in the backend (`POST /api/v1/devices`, admin). The response contains `device_token` **once**.
2. Generate and flash its NVS image (secrets from env/prompt, never argv):
   ```bash
   export VISIONAID_DEVICE_TOKEN=... VISIONAID_WIFI_PASS=...
   python edge/esp32/tools/provision.py --ssid CareHome-Staff \
       --api-base https://api.example.org/api/v1 --out nvs.bin
   python -m esptool --chip esp32s3 write_flash 0x9000 nvs.bin && shred -u nvs.bin
   ```
3. On boot the device joins Wi-Fi, syncs time, and heartbeats. The dashboard moves it from UNPROVISIONED to ONLINE (or DEGRADED with its fault list).

## Reliability behaviours

| Failure | Behaviour |
|---|---|
| Wi-Fi drop | Reconnect via timer with exponential backoff (1 s → 60 s cap, jitter); never gives up; never blocks the event loop |
| Server unreachable during a fall | Event retried 8× with backoff; `event_id` makes retries idempotent |
| Task hang | Task watchdog (15 s) panics → reboot; backend sees the gap as DEGRADED/OFFLINE |
| Camera init failure | Uplink stays alive; fault visible remotely |
| Server has no evidence key | Device discards frames by policy (HTTP 503), alerting unaffected |

## Pending (tracked in ROADMAP.md)

- ESP-DL `pedestrian_detect` integration + JPEG decode path; measure latency/PSRAM
- Frame-size measurement (BUFFER_ARCHITECTURE §6)
- Offline store-and-forward of incident events to SD
- `self_test` / `capture_diagnostics` command handlers
- Signed OTA; NVS + flash encryption; secure boot v2
- Temperature/brown-out reporting (ESP32-S3 internal temperature sensor)
