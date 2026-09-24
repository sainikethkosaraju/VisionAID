# Testing

## Automated today

| Suite | Command | Count | Covers |
|---|---|---|---|
| Backend | `cd backend && pytest` (PostgreSQL required) | 33 | State machine (exhaustive reachability, terminal states, human-response guarantee); confidence levels, fusion, fallback; config validation; device auth; confirmed-fall alerting to the right tier only; idempotency; grouping; observation promotion; caregiver workflow; full escalation ladder → UNRESOLVED → late ack; potential-fall non-recovery promotion; recovery/suspicious expiry; late low verifier score cannot cancel; system-only states unreachable; login uniformity + rate limit; token revocation; RBAC; cross-tenant 404s; credential rotation; heartbeat config/commands; buffer/fault DEGRADED; OFFLINE + supervisor notice; evidence encryption, RBAC, audit, retention 410; analytics from records; device uptime; home screen not all-clear until resolved |
| Edge (host) | `make -C edge/esp32/tests/host` | 12 tests / 21k checks | Ring buffer window/bytes/slots eviction, randomised model check, pinning, seek, zeroing; fall trigger: fall, walking, standing, intentional lying, sitting, bending, recovery, occlusion, 3 Hz — ASan + UBSan, `-Werror -Wconversion` |
| Firmware build | `idf.py build` in `edge/esp32/firmware` | — | Compiles for esp32s3, zero warnings |
| AI pipeline | `pytest ai/tests` | 5 | Subject-level split without leakage, deterministic/stable splits, event-level metrics |
| Migrations | `alembic upgrade head && alembic check` | — | Models ≡ migration |

CI runs all of the above, including the firmware build inside the official `espressif/idf:v5.4.2` container (`.github/workflows/ci.yml`).

**Live end-to-end smoke (manual, Sept 2026):** real uvicorn server with in-process workers — device registration → heartbeat → fall event → WebSocket push to PRIMARY → automatic escalation to SECONDARY after 3 s of wall-clock time → encrypted evidence upload → late acknowledgement. Passed. It surfaced one defect (home screen reported all-clear while an acknowledged fall was still open), now fixed and covered by a test.

**Container image:** the Dockerfile could not be built in the dev environment (Docker Hub rate limit, HTTP 429). Unverified until CI or a local build runs it.

## Not yet tested (and why)
| Area | Blocker | Plan |
|---|---|---|
| Camera, PSRAM, real frame sizes, Wi-Fi reconnection, heartbeat on hardware | No hardware run yet | Hardware bring-up checklist (ROADMAP Phase 5) |
| Detection accuracy (FP/FN) | No detector integrated, no dataset | AI_MODEL.md §5–6 |
| End-to-end latency fall → phone | Mobile app pending | Stopwatch test in staged-fall sessions |
| Load / soak | — | Locust: 200 devices heartbeating, incident bursts; 72 h soak |
| Frontend (navigation, a11y, responsive) | Phase 9 | Playwright + axe-core |

## Hardware bring-up checklist (Phase 5)
1. Boot, PSRAM detected (8 MB), camera PID = OV3660
2. Frame-size measurement matrix (BUFFER_ARCHITECTURE §6)
3. `buffer_effective_seconds` ≥ 60 at chosen config for 24 h, day + night
4. Pull Wi-Fi AP power for 5 min → reconnects, backend shows DEGRADED→OFFLINE→ONLINE, supervisor notified
5. Kill the server during a staged trigger → event delivered on recovery exactly once
6. 72 h soak: no watchdog resets, no heap fragmentation growth (free PSRAM trend)
