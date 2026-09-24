# Database

PostgreSQL 16. Schema source of truth: `backend/app/models/`; migrations: `backend/alembic/versions/` (`alembic check` passes — models and migration agree).

## Conventions
- **UUID v4 primary keys** everywhere (no enumerable IDs across tenants).
- **`timestamptz`, stored UTC.** Facilities carry an IANA timezone; analytics localise (e.g. incidents by local hour).
- **Enums as VARCHAR + CHECK** (`native_enum=False`): adding a state is a constraint migration, not an `ALTER TYPE`.
- Deterministic constraint names (naming convention on `MetaData`) → stable migrations.
- Every tenant table has `facility_id`; every API query filters on it.

## Entities

| Table | Purpose | Key columns / indexes |
|---|---|---|
| `facilities` | Tenant | `timezone`, `settings` JSON (per-facility escalation override) |
| `users` | Staff | unique `email`; `role` ADMIN/SUPERVISOR/NURSE/CAREGIVER; `status`; `token_version` (revocation); `on_duty` |
| `care_assignments` | Who is responsible where | `(facility_id, floor, room)` index; `tier`; `active` |
| `residents` | Monitored people | `(facility_id, floor, room)` index; `risk_level`; `status` |
| `devices` | Cameras | `(facility_id, status)` index; latest telemetry; `pending_commands` JSON; `credential_hash` (SHA-256 of 256-bit secret) |
| `device_status_events` | Append-only status transitions (uptime source) | `(device_id, at)` |
| `incidents` | Fall incidents | unique `(device_id, device_event_id)`; `(facility_id, status)`; `(facility_id, detected_at)`; `next_escalation_at`; location snapshot; all lifecycle timestamps + actors |
| `incident_events` | Immutable timeline | `(incident_id, at)`; transition/observation/escalation; actor |
| `notifications` | Per recipient × channel | `(recipient_id, status)`, `incident_id`; sent/delivered/acknowledged times; `error` |
| `evidence_clips` | Encrypted incident windows | `expires_at` index; `deleted_at`; `sha256` of plaintext |
| `audit_logs` | Security & privacy audit | `(facility_id, at)`; actor user/device; action; IP |

Spec field mapping: `INCIDENT.timestamp` → `detected_at`; `resolution_time` → derived `resolution_seconds` (`resolved_at − alert_sent_at`); `NOTIFICATION.recipient_id/channel/sent_at/delivered_at/acknowledged_at/status` → as named.

## Deliberate choices
- **Heartbeats are not stored as rows.** 100 cameras × 2/min = 288k rows/day of low value. Latest telemetry lives on `devices`; *status transitions* are stored and are sufficient for uptime analytics.
- **Incident location is snapshotted** (room/floor copied at creation) — moving a camera must not rewrite history.
- **Resident matching only when unambiguous** (exactly one active resident in the room); shared rooms stay unassigned rather than guessed.

## Retention
| Data | Retention |
|---|---|
| Evidence payloads | `EVIDENCE_RETENTION_DAYS` (30), purged hourly; row kept with `deleted_at` for audit |
| Incidents, timeline, notifications | Indefinite for now — needs a per-facility policy (PENDING, legal input) |
| Audit logs | Indefinite; archive policy PENDING |

## Growth path
Partition `incident_events`, `notifications`, `audit_logs` by month when any exceeds ~50M rows; move analytics to materialised views at ~100k incidents per query window.
