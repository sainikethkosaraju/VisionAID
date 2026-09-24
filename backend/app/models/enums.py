import enum

from sqlalchemy import Enum as SAEnum


def db_enum(cls: type[enum.Enum]) -> SAEnum:
    # Stored as VARCHAR + CHECK rather than native PG enums: adding a state later is a
    # constraint change, not an ALTER TYPE that cannot run inside a transaction.
    return SAEnum(
        cls,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class UserRole(enum.StrEnum):
    ADMIN = "ADMIN"  # facility administrator
    SUPERVISOR = "SUPERVISOR"
    NURSE = "NURSE"
    CAREGIVER = "CAREGIVER"


class UserStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ResidentStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    DISCHARGED = "DISCHARGED"


class RiskLevel(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DeviceStatus(enum.StrEnum):
    UNPROVISIONED = "UNPROVISIONED"  # registered, never connected
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"  # heartbeats late, or device self-reports a fault
    OFFLINE = "OFFLINE"


class IncidentType(enum.StrEnum):
    FALL = "FALL"


class IncidentStatus(enum.StrEnum):
    SUSPICIOUS = "SUSPICIOUS"
    POTENTIAL_FALL = "POTENTIAL_FALL"
    CONFIRMED_FALL = "CONFIRMED_FALL"
    ALERT_SENT = "ALERT_SENT"
    ESCALATED = "ESCALATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESPONDING = "RESPONDING"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    CANCELLED = "CANCELLED"
    UNRESOLVED = "UNRESOLVED"


class Priority(enum.StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CareTier(enum.StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    SUPERVISOR = "SUPERVISOR"
    EMERGENCY = "EMERGENCY"


class NotificationChannel(enum.StrEnum):
    IN_APP = "in_app"  # persisted + pushed over WebSocket
    PUSH = "push"  # PENDING INTEGRATION (FCM/APNs)
    SMS = "sms"  # PENDING INTEGRATION
    EMAIL = "email"  # PENDING INTEGRATION


class NotificationStatus(enum.StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
