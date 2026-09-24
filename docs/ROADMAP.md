# Roadmap

Legend: ✅ done · 🟡 partial · ⬜ not started

| Phase | Status | Delivered / next | Exit criterion |
|---|---|---|---|
| 0 Audit | ✅ | Repo + environment inspected; hardware verified from primary sources; risks registered | — |
| 1 Hardware architecture | ✅ | Edge/cloud split (Option C); pin map; partition table; memory plan | Confirmed on a physical board (Phase 5) |
| 2 Ephemeral buffer | 🟡 | Ring buffer implemented + host-tested; integrated in firmware; defaults chosen | Measured frame sizes; 24 h ≥ 60 s effective on device |
| 3 Dataset & AI architecture | 🟡 | Two-stage design; dataset registry; leak-proof splits; event metrics | Licences verified; data-collection protocol approved |
| 4 Training pipeline | ⬜ | Ingest → pose cache → temporal model → quantise → export | Verifier beats device-only baseline on held-out staged data |
| 5 ESP32 integration | 🟡 | Firmware compiles: capture, uplink, heartbeat, evidence, watchdog | ESP-DL detector running on DFR1154; bring-up checklist (TESTING.md) passes |
| 6 Backend | ✅ (MVP) | Auth, RBAC, devices, heartbeat, incidents, state machine, confidence engine, escalation, evidence, audit, WebSocket | Multi-node (Redis), refresh tokens |
| 7 Database | ✅ (MVP) | 11 tables, indexes, Alembic 0001 | Retention policies per facility |
| 8 Notifications | 🟡 | In-app + WebSocket, tiered escalation, coverage-gap fallback | FCM/APNs critical alerts; SMS fallback |
| 9 Caregiver app / dashboard | ⬜ | Platform + IA decided (FRONTEND.md) | Ack from lock screen in ≤ 2 taps |
| 10 Analytics | 🟡 | Incident + uptime analytics endpoints from real records | Dashboard views; floor map |
| 11 Security | 🟡 | See SECURITY.md controls table | Flash/NVS encryption, secure boot, signed OTA, KMS, pen-test |
| 12 Cloud deployment | 🟡 | Dockerfile, compose, provider-neutral reference | Staging environment with HA Postgres |
| 13 Testing | 🟡 | 50 automated tests + firmware build in CI | Hardware, load, soak, a11y suites |
| 14 Pilot preparation | ⬜ | — | DPIA, consent pack, install runbook, 1 facility / 5 rooms / 8 weeks |

## Immediate next steps (in order)
1. **Owner:** supply the "Adwith red" value and the source of the "Older Adults Falls Dataset"; confirm hardware is on hand.
2. Hardware bring-up + frame-size measurement (days, not weeks — unblocks everything on the edge).
3. Integrate ESP-DL `pedestrian_detect`; measure latency and PSRAM; record first real bbox trajectories.
4. Staged-fall + ADL capture session on the DFR1154 (consented) → first real evaluation of the trigger.
5. Mobile app skeleton with native critical alerts against the existing API.
