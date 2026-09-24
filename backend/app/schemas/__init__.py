"""Request/response contracts. Input models forbid unknown fields and bound every value."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    CareTier,
    DeviceStatus,
    IncidentStatus,
    IncidentType,
    NotificationChannel,
    NotificationStatus,
    Priority,
    ResidentStatus,
    RiskLevel,
    UserRole,
    UserStatus,
)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Short = Field(min_length=1, max_length=200)
Loc = Field(default=None, max_length=64)


# --- Auth & users ------------------------------------------------------------------------
class LoginIn(In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token type, not a secret
    expires_in: int


class UserCreate(In):
    name: str = Short
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    role: UserRole
    password: str = Field(min_length=12, max_length=256)


class UserUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    role: UserRole | None = None
    status: UserStatus | None = None
    on_duty: bool | None = None


class UserOut(Out):
    id: uuid.UUID
    facility_id: uuid.UUID
    name: str
    email: str
    phone: str | None
    role: UserRole
    status: UserStatus
    on_duty: bool
    created_at: datetime


class AssignmentCreate(In):
    user_id: uuid.UUID
    tier: CareTier
    floor: str | None = Field(default=None, max_length=32)
    room: str | None = Loc


class AssignmentOut(Out):
    id: uuid.UUID
    user_id: uuid.UUID
    tier: CareTier
    floor: str | None
    room: str | None
    active: bool


# --- Residents ---------------------------------------------------------------------------
class ResidentCreate(In):
    name: str = Short
    room: str | None = Loc
    floor: str | None = Field(default=None, max_length=32)
    risk_level: RiskLevel = RiskLevel.MEDIUM


class ResidentUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    room: str | None = Loc
    floor: str | None = Field(default=None, max_length=32)
    risk_level: RiskLevel | None = None
    status: ResidentStatus | None = None


class ResidentOut(Out):
    id: uuid.UUID
    name: str
    room: str | None
    floor: str | None
    risk_level: RiskLevel
    status: ResidentStatus


# --- Devices -----------------------------------------------------------------------------
class DeviceCreate(In):
    name: str = Short
    location: str | None = Field(default=None, max_length=200)
    room: str | None = Loc
    floor: str | None = Field(default=None, max_length=32)


class DeviceUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    room: str | None = Loc
    floor: str | None = Field(default=None, max_length=32)


class DeviceOut(Out):
    id: uuid.UUID
    name: str
    location: str | None
    room: str | None
    floor: str | None
    status: DeviceStatus
    firmware_version: str | None
    model_version: str | None
    last_seen: datetime | None
    last_uptime_seconds: int | None
    last_rssi_dbm: int | None
    last_temperature_c: float | None
    last_power_state: str | None
    last_free_psram_bytes: int | None
    buffer_effective_seconds: float | None
    reported_faults: list
    pending_commands: list


class DeviceCredentialOut(BaseModel):
    device: DeviceOut
    device_token: str = Field(description="Shown once. Provision into device secure storage.")


class DeviceCommandIn(In):
    command: str = Field(pattern="^(restart|self_test|capture_diagnostics)$")


# --- Device gateway (device → server) ------------------------------------------------------
class HeartbeatIn(In):
    timestamp: datetime
    firmware_version: str = Field(max_length=64)
    model_version: str = Field(max_length=64)
    uptime_seconds: int = Field(ge=0)
    rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    temperature_c: float | None = Field(default=None, ge=-40, le=150)
    power_state: str | None = Field(default=None, max_length=32)
    free_psram_bytes: int | None = Field(default=None, ge=0)
    buffer_effective_seconds: float | None = Field(default=None, ge=0, le=3600)
    faults: list[str] = Field(default_factory=list, max_length=16)


class HeartbeatOut(BaseModel):
    server_time: datetime
    config: dict
    commands: list


class FallTriggerIn(In):
    event_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    occurred_at: datetime
    trigger_score: float = Field(ge=0, le=1)
    descent_detected: bool
    immobility_seconds: float = Field(default=0, ge=0, le=3600)
    recovered: bool = False
    features: dict = Field(default_factory=dict)


class ObservationIn(In):
    immobility_seconds: float | None = Field(default=None, ge=0, le=3600)
    recovered: bool | None = None
    trigger_score: float | None = Field(default=None, ge=0, le=1)


class TriggerAck(BaseModel):
    incident_id: uuid.UUID
    status: IncidentStatus
    created: bool
    upload_pre_event_seconds: int
    upload_post_event_seconds: int
    evidence_accepted: bool


# --- Incidents ---------------------------------------------------------------------------
class IncidentOut(Out):
    id: uuid.UUID
    facility_id: uuid.UUID
    resident_id: uuid.UUID | None
    device_id: uuid.UUID
    incident_type: IncidentType
    status: IncidentStatus
    priority: Priority
    room: str | None
    floor: str | None
    detected_at: datetime
    confidence: float
    confidence_level: str
    trigger_score: float
    verifier_score: float | None
    immobility_seconds: float
    trigger_count: int
    escalation_level: int
    next_escalation_at: datetime | None
    confirmed_at: datetime | None
    alert_sent_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    responding_at: datetime | None
    responding_by: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    resolution_note: str | None
    resolution_seconds: float | None


class IncidentEventOut(Out):
    id: uuid.UUID
    kind: str
    from_status: IncidentStatus | None
    to_status: IncidentStatus | None
    actor: str
    actor_user_id: uuid.UUID | None
    note: str | None
    data: dict
    at: datetime


class IncidentDetailOut(IncidentOut):
    timeline: list[IncidentEventOut]
    evidence: list["EvidenceOut"]


class IncidentActionIn(In):
    note: str | None = Field(default=None, max_length=2000)


class EvidenceOut(Out):
    id: uuid.UUID
    segment: str
    frame_count: int
    size_bytes: int
    first_frame_at: datetime | None
    last_frame_at: datetime | None
    expires_at: datetime
    deleted_at: datetime | None


class NotificationOut(Out):
    id: uuid.UUID
    incident_id: uuid.UUID | None
    channel: NotificationChannel
    tier: str | None
    kind: str
    title: str
    body: str
    status: NotificationStatus
    error: str | None
    sent_at: datetime | None
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    created_at: datetime


class FacilityOut(Out):
    id: uuid.UUID
    name: str
    address: str | None
    timezone: str
    settings: dict


IncidentDetailOut.model_rebuild()
