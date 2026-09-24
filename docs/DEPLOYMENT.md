# Deployment

## Local (Docker Compose)

```bash
cp .env.example .env        # fill VISIONAID_JWT_SECRET and VISIONAID_EVIDENCE_KEY
docker compose -f infrastructure/docker-compose.yml --env-file .env up --build
docker compose -f infrastructure/docker-compose.yml exec api alembic upgrade head
docker compose -f infrastructure/docker-compose.yml exec api \
    python -m app.cli create-facility --name "Demo" --admin-email admin@example.org --admin-name Admin
```

Services: `postgres`, `api` (FastAPI + in-process workers). Evidence volume is separate and must be encrypted on the host.

## Cloud reference architecture (provider-neutral)

```
Devices ─TLS─► Load balancer / ingress (TLS termination, WAF, rate limits)
                   │
            API containers (stateless, ≥2)  ──►  Managed PostgreSQL (HA, PITR, encrypted)
                   │                               ▲
            Worker container(s)  ─────────────────┘
                   │
            Redis (pub/sub for WebSocket fan-out, rate limits)
                   │
            Object storage (evidence; SSE + app-level AES-GCM; lifecycle rule = retention)
            Secret manager / KMS (JWT secret, evidence key)
            Push (FCM/APNs), SMS provider — PENDING
```

| Concern | AWS | GCP | Azure |
|---|---|---|---|
| Containers | ECS Fargate | Cloud Run | Container Apps |
| PostgreSQL | RDS / Aurora | Cloud SQL | Flexible Server |
| Redis | ElastiCache | Memorystore | Cache for Redis |
| Evidence | S3 | GCS | Blob Storage |
| Secrets | Secrets Manager + KMS | Secret Manager + KMS | Key Vault |

Provider-specific code is confined to adapters (evidence store, notification providers). Data residency: deploy per region where facilities require it.

## Production checklist (before any pilot)
- [ ] `VISIONAID_ENV=production`, strong secrets from secret manager, `/docs` disabled (automatic)
- [ ] ≥ 2 API instances + separate worker (`VISIONAID_RUN_WORKERS_IN_PROCESS=false`) + Redis fan-out
- [ ] Managed Postgres with PITR backups; restore tested
- [ ] Evidence on encrypted object storage with lifecycle rule
- [ ] Uptime monitoring on `/readyz`; alerting on worker job failures and on *fleet-wide* heartbeat loss
- [ ] Device firmware with flash/NVS encryption and secure boot
- [ ] Load test: 1 facility × 200 cameras heartbeats + burst of 20 concurrent incidents
