"""Device management (staff-facing)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ADMIN, ANY_STAFF, MANAGER, scoped
from app.core.security import hash_device_secret, new_device_secret
from app.database.session import get_db
from app.models import Device, DeviceStatusEvent, User
from app.schemas import DeviceCommandIn, DeviceCreate, DeviceCredentialOut, DeviceOut, DeviceUpdate
from app.services import audit, devices

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
def list_devices(user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)):
    return db.scalars(
        select(Device)
        .where(Device.facility_id == user.facility_id)
        .order_by(Device.floor, Device.room, Device.name)
    ).all()


@router.post("", response_model=DeviceCredentialOut, status_code=201)
def register_device(
    body: DeviceCreate, admin: User = Depends(ADMIN), db: Session = Depends(get_db)
):
    secret = new_device_secret()
    d = Device(
        facility_id=admin.facility_id,
        credential_hash=hash_device_secret(secret),
        **body.model_dump(),
    )
    db.add(d)
    db.flush()
    audit.record(
        db,
        "device.registered",
        facility_id=admin.facility_id,
        user_id=admin.id,
        target_type="device",
        target_id=d.id,
    )
    db.commit()
    return DeviceCredentialOut(
        device=DeviceOut.model_validate(d), device_token=devices.token_for(d.id, secret)
    )


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: uuid.UUID, user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)
):
    return scoped(db.get(Device, device_id), user, "device")


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: uuid.UUID,
    body: DeviceUpdate,
    mgr: User = Depends(MANAGER),
    db: Session = Depends(get_db),
):
    """Rename / move / assign. Existing incidents keep their location snapshot."""
    d = scoped(db.get(Device, device_id), mgr, "device")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(d, k, v)
    audit.record(
        db,
        "device.updated",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="device",
        target_id=d.id,
        details=changes,
    )
    db.commit()
    return d


@router.post("/{device_id}/rotate-credentials", response_model=DeviceCredentialOut)
def rotate(device_id: uuid.UUID, admin: User = Depends(ADMIN), db: Session = Depends(get_db)):
    d = scoped(db.get(Device, device_id), admin, "device")
    secret = devices.rotate_credentials(db, d, admin.id)
    db.commit()
    return DeviceCredentialOut(
        device=DeviceOut.model_validate(d), device_token=devices.token_for(d.id, secret)
    )


@router.post("/{device_id}/commands", response_model=DeviceOut, status_code=202)
def queue_command(
    device_id: uuid.UUID,
    body: DeviceCommandIn,
    mgr: User = Depends(MANAGER),
    db: Session = Depends(get_db),
):
    """Queued; delivered in the device's next heartbeat response (≤ heartbeat interval)."""
    d = scoped(db.get(Device, device_id), mgr, "device")
    d.pending_commands = [*d.pending_commands, {"id": str(uuid.uuid4()), "command": body.command}]
    audit.record(
        db,
        "device.command_queued",
        facility_id=mgr.facility_id,
        user_id=mgr.id,
        target_type="device",
        target_id=d.id,
        details={"command": body.command},
    )
    db.commit()
    return d


@router.get("/{device_id}/status-history")
def status_history(
    device_id: uuid.UUID,
    limit: int = 100,
    user: User = Depends(ANY_STAFF),
    db: Session = Depends(get_db),
):
    scoped(db.get(Device, device_id), user, "device")
    rows = db.scalars(
        select(DeviceStatusEvent)
        .where(DeviceStatusEvent.device_id == device_id)
        .order_by(DeviceStatusEvent.at.desc())
        .limit(min(limit, 500))
    )
    return [
        {"from": r.from_status, "to": r.to_status, "reason": r.reason, "at": r.at} for r in rows
    ]
