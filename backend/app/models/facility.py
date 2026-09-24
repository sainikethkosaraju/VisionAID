import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, created_at_col, uuid_pk
from app.models.enums import CareTier, ResidentStatus, RiskLevel, UserRole, UserStatus, db_enum


class Facility(Base):
    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Per-facility overrides, e.g. {"escalation_policy": "0:primary,45:supervisor"}.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = created_at_col()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(db_enum(UserRole))
    status: Mapped[UserStatus] = mapped_column(db_enum(UserStatus), default=UserStatus.ACTIVE)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Incremented on password change / disable; embedded in JWTs so old tokens die.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    on_duty: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_col()


class CareAssignment(Base):
    """Answers 'who is responsible for this location?'.

    Scope is the most specific of (room, floor, whole facility). room=None & floor=None
    means facility-wide. Resolution walks room → floor → facility for each tier.
    """

    __tablename__ = "care_assignments"
    __table_args__ = (Index("ix_care_assignments_scope", "facility_id", "floor", "room"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tier: Mapped[CareTier] = mapped_column(db_enum(CareTier))
    floor: Mapped[str | None] = mapped_column(String(32))
    room: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_col()


class Resident(Base):
    __tablename__ = "residents"
    __table_args__ = (Index("ix_residents_location", "facility_id", "floor", "room"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id"))
    name: Mapped[str] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(64))
    floor: Mapped[str | None] = mapped_column(String(32))
    risk_level: Mapped[RiskLevel] = mapped_column(db_enum(RiskLevel), default=RiskLevel.MEDIUM)
    status: Mapped[ResidentStatus] = mapped_column(
        db_enum(ResidentStatus), default=ResidentStatus.ACTIVE
    )
    created_at: Mapped[datetime] = created_at_col()
