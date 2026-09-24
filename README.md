# VisionAID

**AI-assisted safety and incident-alert platform for elderly care facilities.**
First sensor: DFRobot ESP32-S3 AI Camera (DFR1154). First capability: fall detection. First interface: caregiver app.

> **Observe temporarily. Detect intelligently. Alert responsibly. Respond quickly. Forget continuously.**

VisionAID is not CCTV. Cameras keep a rolling ~60 s buffer in RAM that is continuously overwritten. Video leaves a room only when a possible fall is detected, and only the incident window (30 s before, 15 s after), encrypted and set to expire.

VisionAID is **not** a medical device, a diagnosis system, an emergency service or a replacement for caregivers. Confidence values are alerting scores, not medical probabilities.

## Status — what is real and what is not

| Component | Status |
|---|---|
| Backend (FastAPI/PostgreSQL): auth, RBAC, devices, heartbeat, incident state machine, confidence engine, escalation, care network, evidence (encrypted, audited, expiring), analytics, WebSocket | ✅ Implemented · 33 tests · live end-to-end smoke passed |
| Ephemeral ring buffer (C) | ✅ Implemented · host-tested under ASan/UBSan |
| On-device temporal fall trigger (C, heuristic v0) | 🟡 Implemented + tested on **synthetic** trajectories only — not validated on real falls |
| ESP32-S3 firmware (capture, uplink, heartbeat, evidence upload, watchdog) | 🟡 Compiles for esp32s3 (0 warnings) — **not yet run on hardware** |
| On-device person detector (ESP-DL) | ⬜ PENDING INTEGRATION — device reports `detector_unavailable` and shows as DEGRADED |
| Server-side fall verifier (pose + temporal) | ⬜ PENDING — engine falls back to device score, never fabricates one |
| Push / SMS / email notifications | ⬜ PENDING — in-app + WebSocket work |
| Caregiver mobile app & dashboard | ⬜ PENDING (Phase 9) |
| Datasets / trained models | ⬜ PENDING — licences unverified; own data collection required |

## Architecture in one picture

```
DFR1154 camera ──(PSRAM ring, 60 s, never flash)──► person detector ─► temporal fall trigger
      │  heartbeat 30 s · 300-byte fall event first · evidence only on trigger
      ▼
FastAPI backend ─► confidence engine ─► incident state machine ─► tiered escalation
      │                (verifier: PENDING)        (room → floor → facility responsibility)
      ▼
PostgreSQL · encrypted evidence store · WebSocket ─► caregiver app (PENDING)
```

Why hybrid: Espressif benchmarks YOLO11n-pose at **≈27.8 s/frame** on ESP32-S3 (not viable) and person detection at **≈130 ms** (viable). So the device does recall-oriented triggering and privacy-preserving buffering; the server does precision. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository

```
ai/          dataset registry, leak-proof splits, event-level metrics
backend/     FastAPI service, Alembic migrations, tests
edge/esp32/  firmware (ESP-IDF), portable components, host tests, tools
docs/        architecture, buffer, AI, dataset, ESP32, backend, frontend,
             database, API, security, deployment, testing, roadmap, business
infrastructure/  docker compose
frontend/ mobile/  PENDING
```

## Quick start

```bash
# Backend (PostgreSQL required)
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev]"
cp .env.example .env   # set VISIONAID_JWT_SECRET (+ VISIONAID_EVIDENCE_KEY to accept evidence)
cd backend && alembic upgrade head && uvicorn app.main:app --reload

# Tests
cd backend && pytest
make -C edge/esp32/tests/host
python -m pytest ai/tests

# Firmware (ESP-IDF v5.4)
cd edge/esp32/firmware && idf.py set-target esp32s3 && idf.py build
```

## Documentation
[Architecture & risks](docs/ARCHITECTURE.md) · [Buffer](docs/BUFFER_ARCHITECTURE.md) · [AI model](docs/AI_MODEL.md) · [Dataset](docs/DATASET.md) · [ESP32](docs/ESP32_SETUP.md) · [Backend](docs/BACKEND.md) · [Frontend](docs/FRONTEND.md) · [Database](docs/DATABASE.md) · [API](docs/API.md) · [Security](docs/SECURITY.md) · [Deployment](docs/DEPLOYMENT.md) · [Testing](docs/TESTING.md) · [Roadmap](docs/ROADMAP.md) · [Business model](docs/BUSINESS_MODEL.md)
