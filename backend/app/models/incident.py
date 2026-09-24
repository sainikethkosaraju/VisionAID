import uuid
from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.database.base import Base, created_at_col, uuid_pk
from app.models.enums import (
    IncidentStatus,
    IncidentType,
    NotificationChannel,
    NotificationStatus,
    Priority,
    db_enum,
)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("device_id", "device_event_id", name="uq_incidents_device_event"),
        Index("ix_incidents_facility_status", "facility_id", "status"),
        Index("ix_incidents_facility_detected", "facility_id", "detected_at"),
        Index("ix_incidents_escalation_due", "next_escalation_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id"))
    resident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("residents.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    device_event_id: Mapped[str] = mapped_column(String(64))  # idempotency key from device
    incident_type: Mapped[IncidentType] = mapped_column(
        db_enum(IncidentType), default=IncidentType.FALL
    )
    status: Mapped[IncidentStatus] = mapped_column(db_enum(IncidentStatus))
    priority: Mapped[Priority] = mapped_column(db_enum(Priority), default=Priority.LOW)
    # Location snapshot: the device may be moved later; the incident must not move.
    room: Mapped[str | None] = mapped_column(String(64))
    floor: Mapped[str | None] = mapped_column(String(32))

    detected_at: Mapped[datetime]  # device clock, corrected by server offset
    confidence: Mapped[float] = mapped_column(Float)  # engine output, NOT medical
    confidence_level: Mapped[str] = mapped_column(String(16))
    trigger_score: Mapped[float] = mapped_column(Float)  # device-side heuristic
    verifier_score: Mapped[float | None] = mapped_column(Float)  # server model, if run
    immobility_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    trigger_count: Mapped[int] = mapped_column(Integer, default=1)  # grouped re-triggers
    features: Mapped[dict] = mapped_column(JSON, default=dict)

    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    next_escalation_at: Mapped[datetime | None]

    confirmed_at: Mapped[datetime | None]
    alert_sent_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    responding_at: Mapped[datetime | None]
    responding_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution_note: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    @property
    def resolution_seconds(self) -> float | None:
        if self.resolved_at and self.alert_sent_at:
            return (self.resolved_at - self.alert_sent_at).total_seconds()
        return None


class IncidentEvent(Base):
    """Immutable timeline of every state transition and observation."""

    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident_at", "incident_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # transition | observation | escalation
    from_status: Mapped[IncidentStatus | None] = mapped_column(db_enum(IncidentStatus))
    to_status: Mapped[IncidentStatus | None] = mapped_column(db_enum(IncidentStatus))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(32))  # user | device | system
    note: Mapped[str | None] = mapped_column(String(2000))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = created_at_col()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient_status", "recipient_id", "status"),
        Index("ix_notifications_incident", "incident_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[NotificationChannel] = mapped_column(db_enum(NotificationChannel))
    tier: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32), default="incident")  # incident | device
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(1000))
    status: Mapped[NotificationStatus] = mapped_column(
        db_enum(NotificationStatus), default=NotificationStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_col()


class EvidenceClip(Base):
    """Preserved incident window. Payload lives in the evidence store, encrypted."""

    __tablename__ = "evidence_clips"
    __table_args__ = (Index("ix_evidence_clips_expires", "expires_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(300))
    segment: Mapped[str] = mapped_column(String(16))  # pre | post
    frame_count: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    first_frame_at: Mapped[datetime | None]
    last_frame_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_col()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_facility_at", "facility_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("facilities.id"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = created_at_col()
