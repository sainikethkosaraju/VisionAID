# API

Base path `/api/v1`. Interactive OpenAPI at `/docs` (disabled in production). All staff endpoints require `Authorization: Bearer <JWT>`; device endpoints require `Authorization: Bearer <device_uuid>.<secret>`. Objects from another facility return **404**.

## Staff API

| Method | Path | Roles | Purpose |
|---|---|---|---|
| POST | `/auth/login` | public (rate-limited) | → `{access_token, expires_in}` |
| GET | `/auth/me` | any | Current user |
| GET/POST | `/users` | GET: admin, supervisor · POST: admin | List / create staff |
| PATCH | `/users/{id}` | admin | Role/status/duty changes (role or disable revokes sessions) |
| PATCH | `/me/duty?on_duty=` | any | Go on/off duty (off-duty users are not paged) |
| GET/POST | `/care-assignments` | GET any · POST admin, supervisor | Care network |
| DELETE | `/care-assignments/{id}` | admin, supervisor | Deactivate |
| GET/POST/PATCH | `/residents[/{id}]` | GET any · write admin, supervisor | Residents |
| GET | `/devices`, `/devices/{id}` | any | Device list / detail with telemetry |
| POST | `/devices` | admin | Register → `device_token` shown **once** |
| PATCH | `/devices/{id}` | admin, supervisor | Rename / move / assign |
| POST | `/devices/{id}/rotate-credentials` | admin | New token; old one stops working immediately |
| POST | `/devices/{id}/commands` | admin, supervisor | `restart` · `self_test` · `capture_diagnostics` (delivered at next heartbeat) |
| GET | `/devices/{id}/status-history` | any | Status transitions |
| GET | `/incidents?active=&status_=&since=&limit=` | any | List |
| GET | `/incidents/{id}` | any | Detail + timeline + evidence metadata |
| POST | `/incidents/{id}/acknowledge` · `respond` · `resolve` · `false_positive` | any staff | Body `{note?}`; 409 on invalid transition |
| GET | `/evidence/{clip_id}/frames/{n}` | admin, supervisor, nurse | JPEG; audited; `no-store` |
| GET | `/notifications?unacknowledged=` | any | My notifications |
| GET | `/facility`, `/facility/overview` | any | "Is everyone safe?" summary |
| GET | `/analytics/incidents?days=` · `/analytics/devices?days=` | admin, supervisor | Computed metrics |
| WS | `/ws` | any | First message `{"token": "<JWT>"}` → facility-scoped events |

## Device protocol

```
Device                                    Backend
  │ POST /device/heartbeat (every 30 s) ─────►│ status ONLINE/DEGRADED, telemetry
  │◄──── {server_time, config{buffer,pre,post,heartbeat}, commands[]}
  │
  │ fall_trigger fires
  │ POST /device/events/fall ────────────────►│ incident (idempotent on event_id)
  │◄──── {incident_id, status, upload_pre_event_seconds, evidence_accepted}
  │ POST /device/incidents/{id}/evidence?segment=pre   (multipart JPEG, filename=<epoch_ms>.jpg)
  │ POST /device/incidents/{id}/observations {immobility_seconds, recovered}
  │ … post-event window …
  │ POST /device/incidents/{id}/evidence?segment=post
```

### `POST /device/events/fall`
```json
{"event_id": "a1b2c3-123456789", "occurred_at": "2026-09-24T10:15:03.120Z",
 "trigger_score": 0.78, "descent_detected": true, "immobility_seconds": 0,
 "recovered": false, "features": {}}
```
`occurred_at` more than ±300 s from server time is replaced by server time.

### WebSocket events
```json
{"type":"incident","incident_id":"…","status":"ALERT_SENT","priority":"CRITICAL","room":"101","floor":"1","confidence":0.82,"escalation_level":0}
{"type":"notification","recipient_id":"…","incident_id":"…","title":"Possible fall — Room 101, Floor 1","tier":"PRIMARY","kind":"incident"}
{"type":"device","device_id":"…","status":"OFFLINE","reason":"no heartbeat for 125s"}
```
Events are emitted only after the database transaction commits.
