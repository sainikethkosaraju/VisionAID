# Backend

FastAPI · SQLAlchemy 2 · PostgreSQL 16 · Alembic · Pydantic v2. **33 tests passing against real PostgreSQL.**

## Structure

```
backend/app/
  core/        config (env-driven), security (Argon2id, JWT, device creds), rate limiter
  database/    engine/session, declarative base with deterministic constraint names
  models/      facility, user, care_assignment, resident, device, device_status_event,
               incident, incident_event, notification, evidence_clip, audit_log
  schemas/     request/response contracts (extra fields forbidden, all values bounded)
  services/    state_machine, confidence, incidents, notifications, devices,
               evidence, verifier (PENDING), analytics, realtime, audit
  api/routes/  auth, people, devices, gateway (device-facing), incidents, facility, ws
  workers/     scheduler: escalation, expiry, device health, evidence retention
  cli.py       operator bootstrap (no self-service sign-up)
alembic/       migrations (0001 initial schema, round-trip verified)
tests/         rules, incident flow, platform (auth, tenancy, health, evidence, analytics)
```

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cp .env.example .env    # then set VISIONAID_JWT_SECRET and VISIONAID_EVIDENCE_KEY
cd backend
alembic upgrade head
python -m app.cli create-facility --name "Demo Home" --timezone Asia/Kolkata \
    --admin-email admin@example.org --admin-name Admin
uvicorn app.main:app --reload
# tests (needs a PostgreSQL database; see conftest.py)
pytest
```

## Incident state machine

```
             ┌──────────► CANCELLED (expired / recovered)
SUSPICIOUS ──┤
     │       └─► POTENTIAL_FALL ──► CONFIRMED_FALL ──► ALERT_SENT ──► ESCALATED ⟲
     └───────────────────┘                                 │             │
                                                           ▼             ▼
                    ACKNOWLEDGED ◄──────────────────── (human) ◄── UNRESOLVED
                         │                                  ▲ (ladder exhausted)
                         ▼
                    RESPONDING ──► RESOLVED | FALSE_POSITIVE
```

Rules (`services/state_machine.py`, exhaustively tested):
- Terminal: RESOLVED, FALSE_POSITIVE, CANCELLED.
- From ALERT_SENT / ESCALATED / UNRESOLVED **every human response is always allowed** — no state blocks a caregiver.
- Users can only request `acknowledge`, `respond`, `resolve`, `false_positive`; confirmation, alerting, escalation and cancellation are system-only.
- Every transition writes an `incident_events` row in the same transaction.

## Care network & escalation

`care_assignments` answers *who is responsible for this location*: per tier (PRIMARY, SECONDARY, SUPERVISOR, EMERGENCY), the most specific active assignment wins — room → floor → facility. If a tier is empty, on-duty supervisors/admins are paged and the **coverage gap is recorded on the incident**. Off-duty and disabled users are never paged. Recipients already paged for an incident aren't paged again by a later tier.

Default ladder `VISIONAID_ESCALATION_POLICY=0:primary,30:secondary,60:supervisor,120:emergency`; per-facility override in `facilities.settings.escalation_policy`. After the last tier plus `UNRESOLVED_AFTER_SECONDS` (300) → UNRESOLVED and supervisors are re-paged.

## Smart alerts

| Mechanism | Implementation |
|---|---|
| Idempotency | `(device_id, device_event_id)` unique — device retries never duplicate |
| Grouping | New trigger on a device with an open incident within `INCIDENT_GROUP_SECONDS` → `trigger_count++`, no new alert |
| No suppression of new falls | An idle device's trigger always creates an incident, regardless of past false positives |
| Priority | LOW (suspicious) → HIGH (potential) → CRITICAL (alerted) |
| Acknowledgement tracking | Per-notification `acknowledged_at`; any human action clears `next_escalation_at` |

## Configuration (`VISIONAID_*`)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local postgres | |
| `JWT_SECRET` | **required** | ≥ 32 chars in production (validated) |
| `ACCESS_TOKEN_MINUTES` | 60 | |
| `BUFFER_SECONDS` / `PRE_EVENT_SECONDS` / `POST_EVENT_SECONDS` | 60 / 30 / 15 | pushed to devices |
| `DEVICE_HEARTBEAT_SECONDS` | 30 | DEGRADED after 2 missed, OFFLINE after 4 |
| `CONF_SUSPICIOUS` / `CONF_POTENTIAL` | 0.35 / 0.60 | heuristic, tune on data |
| `VERIFIER_WEIGHT` | 0.7 | |
| `IMMOBILITY_CONFIRM_SECONDS` | 10 | |
| `POTENTIAL_FALL_TIMEOUT_SECONDS` | 45 | no recovery → alert |
| `INCIDENT_GROUP_SECONDS` / `SUSPICIOUS_EXPIRY_SECONDS` | 120 / 120 | |
| `ESCALATION_POLICY` / `UNRESOLVED_AFTER_SECONDS` | see above / 300 | |
| `NOTIFICATION_CHANNELS` | `["in_app"]` | push/sms/email: PENDING providers |
| `EVIDENCE_KEY` | unset → evidence refused | 32-byte urlsafe base64 |
| `EVIDENCE_RETENTION_DAYS` | 30 | |
| `RUN_WORKERS_IN_PROCESS` | true | false → run `python -m app.workers.scheduler` separately |

## Known limitations
- Single-node real-time fan-out (in-process WebSocket hub). Multi-node needs Redis pub/sub (interface ready).
- In-process login rate limiter; Redis-backed for multi-node.
- No refresh tokens yet (access token + server-side revocation via `token_version`).
- Push/SMS/email providers not integrated; attempts on unconfigured channels are recorded as FAILED, never silently dropped.
