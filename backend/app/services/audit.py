import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    action: str,
    *,
    facility_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            facility_id=facility_id,
            actor_user_id=user_id,
            actor_device_id=device_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
            ip=ip,
        )
    )
