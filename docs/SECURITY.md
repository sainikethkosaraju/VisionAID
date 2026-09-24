# Security & Privacy

Report vulnerabilities privately to the repository owner (see `.github/SECURITY.md`). Do not open public issues for security problems.

## Threat model (summary)

| Asset | Threats | Controls |
|---|---|---|
| Resident imagery | Exfiltration, voyeurism, retention creep | No continuous upload; PSRAM-only ring with zeroing; evidence only on trigger; AES-256-GCM at rest (AAD = incident ID); retention purge; clinical-role access; per-frame access audit; `no-store` |
| Staff accounts | Credential stuffing, session theft | Argon2id; uniform login errors + timing equaliser; per-IP+email rate limit; JWT expiry; `token_version` revocation on disable/role change |
| Devices | Impersonation, cloned camera, replay | 256-bit per-device secret over TLS, SHA-256 at rest, constant-time compare, rotation endpoint; idempotent event IDs; device can only touch its own incidents |
| Tenancy | Cross-facility access | Facility scoping on every query; foreign IDs → 404 (tested) |
| Alerting integrity | Silent failure | Heartbeat → DEGRADED/OFFLINE + supervisor page; worker jobs isolated; watchdog reboot on device |

## Controls status

| Control | Status |
|---|---|
| HTTPS / TLS 1.2+ | Device: enforced (`https://` required, CA bundle, no insecure mode). Server: terminate TLS at ingress; HSTS in production |
| Password hashing (Argon2id) | ✅ |
| JWT with expiry, typed tokens, required claims | ✅ |
| RBAC (ADMIN, SUPERVISOR, NURSE, CAREGIVER) | ✅ tested |
| Input validation (Pydantic, extra fields forbidden, bounded values) | ✅ |
| Rate limiting (login) | ✅ in-process · Redis-backed for multi-node: PENDING |
| Audit logging (logins, failures, user/device/resident changes, commands, incident actions, evidence views) | ✅ |
| Evidence encryption at rest | ✅ app-level AES-256-GCM; plus provider disk/object encryption in cloud |
| Secrets via environment only; startup validation | ✅ nothing committed; `.env` git-ignored |
| Device credential rotation | ✅ |
| Security headers (nosniff, no-referrer, frame DENY, HSTS in prod) | ✅ |
| Refresh tokens / MFA for admins | PENDING |
| mTLS or signed device requests (HMAC with timestamp) | PENDING |
| ESP32 flash encryption + NVS encryption + secure boot v2 | PENDING (required before pilot) |
| Signed OTA | PENDING |
| Key management (KMS, key rotation for evidence) | PENDING — current key is a single env secret |
| Dependency / secret scanning in CI | Dependabot configured; enable GitHub secret scanning + push protection in repo settings |

## Privacy by design
- **No live view.** There is deliberately no endpoint to stream a camera.
- **Minimum necessary evidence:** 30 s before + 15 s after, HVGA, only on trigger.
- **Evidence is refused, not stored in plaintext**, when no key is configured.
- **Expiry is enforced**, and access after purge returns 410.
- Before any pilot: Data Protection Impact Assessment; resident/family consent flow; signage; data processing agreement with the facility; jurisdiction review (e.g. India DPDP Act 2023, GDPR, HIPAA where applicable — which apply depends on where facilities operate).

## Secrets handling
- Generate: `python -c "import secrets;print(secrets.token_urlsafe(48))"` (JWT); `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` (evidence key).
- Store in the cloud provider's secret manager; inject as env vars. Never in images, compose files, or the repo.
- Rotation: JWT secret rotation invalidates sessions (acceptable); evidence key rotation requires re-encryption or key versioning (PENDING).
