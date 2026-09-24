# VisionAID — System Architecture

> Status: **Phase 0 audit complete; Phase 2 (buffer), Phase 6–7 (backend/database) core implemented.**
> Everything labelled PENDING INTEGRATION below is not yet functional and is not presented as such.

## 1. Phase 0 audit — starting point

| Item | Finding |
|---|---|
| Repository | Greenfield: one commit, an empty `README.md`. No prior code, data, models or decisions to preserve. |
| Cloud dev environment | Linux x86-64, 4 vCPU, 15 GB RAM, Python 3.11, Node 22, PostgreSQL 16, Redis 7, Docker 29, gcc. No GPU (training must run elsewhere). |
| Firmware toolchain | ESP-IDF v5.4.2 installed during this phase; firmware **compiles** for `esp32s3` with zero warnings. Not yet flashed to hardware. |
| Hardware in hand | Unknown. No measurements from a physical DFR1154 exist yet. Every frame-size figure below is an estimate until measured. |

## 2. Verified hardware envelope (DFRobot DFR1154)

Source: [DFRobot wiki](https://wiki.dfrobot.com/SKU_DFR1154_ESP32_S3_AI_CAM), [product page](https://www.dfrobot.com/product-2899.html).

| | |
|---|---|
| MCU | ESP32-S3R8, dual-core Xtensa LX7 @ 240 MHz, vector (SIMD) extensions used by ESP-NN/ESP-DL |
| Memory | 512 KB SRAM, **8 MB octal PSRAM**, **16 MB flash** |
| Camera | OV3660, 3 MP, **160° wide-angle**, visible + 940 nm IR, IR illuminator (GPIO47) |
| Other | PDM mic, I²S amp (MAX98357), LTR-308 ambient light sensor, microSD slot, Wi-Fi 802.11 b/g/n 2.4 GHz, BLE 5 |
| Power | 5 V USB-C or 3.7–15 V VIN; −10…60 °C |
| Price | $18.90 retail, $17.50 at 10+ (verified Sept 2026) |

## 3. The decisive constraint: what the ESP32-S3 can actually infer

Espressif's own published ESP-DL benchmarks on ESP32-S3:

| Model | Input | Latency on ESP32-S3 | Usable in real time? |
|---|---|---|---|
| `pedestrian_detect` (pico_s8_v1) | 224×224 | 9 + **118** + 2 ms ≈ 130 ms | **Yes** — ≈7 Hz max, one core |
| `coco_pose` (YOLO11n-pose s8) | 640×640 | **≈27,800 ms** | **No** — 0.036 fps |

Pose estimation on the device is not viable. Person *detection* is.

## 4. Edge vs cloud decision — Option C (hybrid), recall-first on the edge

| Criterion | A. Full edge | B. Edge pre-proc + cloud inference | **C. Hybrid (chosen)** |
|---|---|---|---|
| Accuracy | Limited to bbox heuristics | Best (pose/temporal models) | Best: server verifies |
| Privacy | Best | **Poor — continuous video leaves the room** | Video leaves the device only after a trigger |
| Latency | Lowest | Network-bound, every frame | Alert path is a 300-byte JSON |
| Network dependency | None | Total | Trigger + buffer survive outages |
| Cloud cost | ~0 | High (continuous streams) | Low (event-driven) |
| Compute feasibility | Pose infeasible (§3) | Feasible | Feasible |

**How C works:**

1. **Device (always on):** camera → ephemeral ring buffer (PSRAM only) → person detector (~4 Hz) → temporal fall trigger (bbox velocity, height collapse, aspect flip, post-fall immobility/recovery). Tuned for **recall**.
2. **Trigger:** device sends a tiny event immediately (alert path never waits for images), pins the buffer, then uploads the pre-event window and later the post-event window.
3. **Backend:** confidence engine fuses device evidence with the **server-side verifier** (pose + temporal model, PENDING) and drives the incident state machine, escalation and notifications.

**Fail-safe principle:** every degradation biases toward alerting, not silence. Verifier down → device score alone. Potential fall without observed recovery → alert on timeout. Device silent → marked OFFLINE and supervisors told the area is unmonitored.

## 5. Component map

```
┌────────────── DFR1154 camera (per room) ──────────────┐
│ OV3660 → JPEG → [ring buffer, PSRAM, 60 s, 4.5 MB]    │
│               ↘ person detector (ESP-DL, PENDING)     │
│                  ↘ fall_trigger (temporal FSM)        │
│ uplink: HTTPS + CA bundle, device token, retry/backoff│
└───────┬───────────────────────────────────────────────┘
        │ heartbeat (30 s) · fall event · observations · evidence (on trigger only)
        ▼
┌──────────────────── Backend (FastAPI) ─────────────────────┐
│ device gateway ─► incident service ─► confidence engine    │
│                     │  state machine   ▲ verifier (PENDING)│
│                     ▼                                      │
│   escalation / expiry / device-health workers (5 s cycle)  │
│   notifications: in-app+WebSocket ✔ · push/SMS/email ✗     │
│   evidence store: AES-256-GCM, retention purge, audited    │
│   PostgreSQL  ·  (Redis for multi-node fan-out, later)     │
└───────┬────────────────────────────────────────────────────┘
        ▼ REST + WebSocket
  Caregiver mobile app / facility dashboard (Phase 9, PENDING)
```

## 6. Data flows

**Alert path (latency-critical)**
`fall_trigger fires` → `POST /device/events/fall` (idempotent `event_id`) → incident created → confidence engine → if CONFIRMED: notify PRIMARY tier for the room → `ALERT_SENT` → escalation clock starts.

**Evidence path (best-effort)** — pre-event frames (30 s) uploaded in 40-frame batches → encrypted at rest → verifier (when deployed) → observation → confidence re-assessed. Post-event frames (15 s) follow. Frames are unpinned and forgotten on the device afterwards.

**Health path** — heartbeat every 30 s carries firmware/model version, uptime, RSSI, free PSRAM, *actual* buffer seconds and faults. The response carries configuration (buffer windows) and queued commands. Missing 2 heartbeats → DEGRADED; 4 → OFFLINE + supervisor notification.

## 7. Major assumptions (explicit)

| # | Assumption | Consequence if wrong | How it gets resolved |
|---|---|---|---|
| A1 | OV3660 HVGA JPEG averages ≤ 18 KB | Buffer holds < 60 s; device self-reports DEGRADED | Measure on hardware (BUFFER_ARCHITECTURE §6) |
| A2 | ESP-DL pedestrian detector detects people from a ceiling-corner, 160° view, incl. lying people | Trigger recall collapses | Record on-site footage; fine-tune / swap detector |
| A3 | A bbox-temporal heuristic can reach high recall at tolerable false-trigger rates | Too many server verifications or misses | Staged-fall capture + evaluation harness (`ai/evaluation`) |
| A4 | Facility Wi-Fi reaches every room at usable RSSI | Missed heartbeats / delayed alerts | Site survey in pilot; RSSI is reported per device |
| A5 | Caregivers carry a phone that can receive critical alerts | Alerts go unseen | Native app with critical-alert entitlements (Phase 9) |
| A6 | One camera ↔ one room | Resident matching ambiguous | Room-level resident matching only when unambiguous |

## 8. Risk register

### Technical
| Risk | Severity | Mitigation |
|---|---|---|
| Fisheye (160°) distortion breaks bbox geometry at image edges | High | Normalise by per-person upright height; add lens undistortion or per-zone thresholds after measurement |
| Occlusion by beds/furniture hides the fall or the fallen person | High | Occlusion never counts as recovery; un-recovered potential falls escalate on timeout |
| Detector trained on upright pedestrians misses lying/fallen people | High | Treat "person disappears low in frame after descent" as signal; collect lying-pose data |
| IR night mode noise inflates JPEG size and degrades detection | Medium | Measure night frames separately; buffer self-reports effective seconds |
| Server verifier not yet trained | High | Engine is explicit about verifier absence; no score is fabricated |
| Single-node backend is a single point of failure | High (production) | Stateless API + worker split + Redis fan-out are designed in; deploy HA before pilot |
| Clock skew on devices | Low | SNTP + server clamps timestamps beyond ±300 s |

### Business
| Risk | Mitigation |
|---|---|
| Public fall datasets are research-only licences → cannot train a commercial model on them | Own consented data collection is on the critical path (DATASET.md) |
| False alarms cause alert fatigue and churn | Measure false alarms / camera / day as a primary KPI; server verification; grouping |
| Medical-device regulation if marketed as diagnostic | Position strictly as an AI-assisted safety alert (§40 of spec); regulatory review before launch in each market |
| Camera-in-bedroom acceptance by residents/families | Privacy-first design is the product; consent workflow, no live view, evidence expiry |
| Competition from radar/wearable fall detection | Differentiate on workflow + facility intelligence + cost (BUSINESS_MODEL.md) |

### Privacy & security
| Risk | Mitigation (implemented unless marked) |
|---|---|
| Video exfiltration | No continuous upload; evidence only on trigger; AES-256-GCM at rest; evidence refused if no key configured |
| Unauthorised viewing of evidence | Clinical roles only; every frame view audited; `Cache-Control: no-store` |
| Device impersonation | Per-device 256-bit secrets, hashed server-side, rotatable. mTLS: PENDING |
| Stolen camera reveals Wi-Fi/token | NVS + flash encryption: PENDING (documented in SECURITY.md) |
| Cross-facility data leak | Every query facility-scoped; foreign IDs return 404 (tested) |
| Credential stuffing | Argon2id hashing, uniform errors, rate limiting, token revocation on disable |
