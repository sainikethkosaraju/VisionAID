"""Runtime configuration.

Every tunable that affects safety behaviour (buffer windows, confidence thresholds,
escalation timing, heartbeat cadence) is read from the environment with the
VISIONAID_ prefix. Nothing here is a secret default: JWT and evidence keys must be
supplied by the deployment.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_escalation(spec: str) -> list[tuple[int, str]]:
    """Parse '0:primary,30:secondary,...' into sorted (delay_seconds, TIER) pairs."""
    steps: list[tuple[int, str]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        delay, tier = part.split(":", 1)
        steps.append((int(delay), tier.strip().upper()))
    steps.sort(key=lambda s: s[0])
    if not steps or steps[0][0] != 0:
        raise ValueError("escalation policy must start with a step at 0 seconds")
    return steps


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISIONAID_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://visionaid:visionaid@localhost:5432/visionaid"
    cors_origins: list[str] = Field(default_factory=list)

    # --- Auth -------------------------------------------------------------------------
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    login_rate_limit_per_minute: int = 10

    # --- Ephemeral vision buffer (pushed to devices via heartbeat response) -----------
    buffer_seconds: int = 60
    pre_event_seconds: int = 30
    post_event_seconds: int = 15

    # --- Device health ----------------------------------------------------------------
    device_heartbeat_seconds: int = 30
    device_degraded_after_missed: int = 2
    device_offline_after_missed: int = 4

    # --- Confidence engine (heuristic policy v0 — NOT a medical probability) ----------
    conf_suspicious: float = 0.35
    conf_potential: float = 0.60
    verifier_weight: float = 0.7
    immobility_confirm_seconds: int = 10

    # --- Incidents & alerts -----------------------------------------------------------
    incident_group_seconds: int = 120
    suspicious_expiry_seconds: int = 120
    # A POTENTIAL_FALL with no observed recovery (person upright again) is promoted to an
    # alert after this long — covers falls where the person keeps moving but cannot rise.
    potential_fall_timeout_seconds: int = 45
    escalation_policy: str = "0:primary,30:secondary,60:supervisor,120:emergency"
    unresolved_after_seconds: int = 300
    notification_channels: list[str] = Field(default_factory=lambda: ["in_app"])
    worker_interval_seconds: float = 5.0
    run_workers_in_process: bool = True

    # --- Evidence ---------------------------------------------------------------------
    evidence_dir: str = "./var/evidence"
    evidence_key: SecretStr | None = None  # 32-byte urlsafe-base64 AES-256-GCM key
    evidence_retention_days: int = 30
    evidence_max_upload_bytes: int = 12 * 1024 * 1024

    @field_validator("conf_suspicious", "conf_potential", "verifier_weight")
    @classmethod
    def _unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("must be within [0, 1]")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.pre_event_seconds > self.buffer_seconds:
            raise ValueError("pre_event_seconds cannot exceed buffer_seconds")
        if self.conf_suspicious >= self.conf_potential:
            raise ValueError("conf_suspicious must be below conf_potential")
        if self.device_degraded_after_missed >= self.device_offline_after_missed:
            raise ValueError("degraded threshold must be below offline threshold")
        if self.env == "production" and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("jwt_secret must be at least 32 characters in production")
        parse_escalation(self.escalation_policy)
        return self

    @property
    def escalation_steps(self) -> list[tuple[int, str]]:
        return parse_escalation(self.escalation_policy)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
