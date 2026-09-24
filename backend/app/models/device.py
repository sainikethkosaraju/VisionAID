import uuid
from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, created_at_col, uuid_pk
from app.models.enums import DeviceStatus, db_enum


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (Index("ix_devices_facility_status", "facility_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id"))
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(64))
    floor: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[DeviceStatus] = mapped_column(
        db_enum(DeviceStatus), default=DeviceStatus.UNPROVISIONED
    )
    firmware_version: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(64))
    last_seen: Mapped[datetime | None]
    # Latest heartbeat telemetry (full history is not stored; status transitions are).
    last_uptime_seconds: Mapped[int | None] = mapped_column(Integer)
    last_rssi_dbm: Mapped[int | None] = mapped_column(Integer)
    last_temperature_c: Mapped[float | None] = mapped_column(Float)
    last_power_state: Mapped[str | None] = mapped_column(String(32))
    last_free_psram_bytes: Mapped[int | None] = mapped_column(Integer)
    buffer_effective_seconds: Mapped[float | None] = mapped_column(Float)
    reported_faults: Mapped[list] = mapped_column(JSON, default=list)
    # Commands queued for delivery in the next heartbeat response (restart, test, ...).
    pending_commands: Mapped[list] = mapped_column(JSON, default=list)
    credential_hash: Mapped[str] = mapped_column(String(64))
    credential_rotated_at: Mapped[datetime] = created_at_col()
    created_at: Mapped[datetime] = created_at_col()


class DeviceStatusEvent(Base):
    """Append-only status transitions; source of truth for uptime analytics."""

    __tablename__ = "device_status_events"
    __table_args__ = (Index("ix_device_status_events_device_at", "device_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    from_status: Mapped[DeviceStatus | None] = mapped_column(db_enum(DeviceStatus))
    to_status: Mapped[DeviceStatus] = mapped_column(db_enum(DeviceStatus))
    reason: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = created_at_col()
