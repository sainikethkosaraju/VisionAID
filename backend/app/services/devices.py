"""Device heartbeat, health sweep and credentials. A safety device must not fail silently."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_device_secret, new_device_secret
from app.core.timeutil import utcnow
from app.models import Device, DeviceStatusEvent
from app.models.enums import CareTier, DeviceStatus
from app.services import audit, notifications
from app.services.realtime import queue_event


@dataclass
class Heartbeat:
    firmware_version: str
    model_version: str
    uptime_seconds: int
    rssi_dbm: int | None = None
    temperature_c: float | None = None
    power_state: str | None = None
    free_psram_bytes: int | None = None
    buffer_effective_seconds: float | None = None
    faults: list[str] | None = None


def set_status(db: Session, device: Device, to: DeviceStatus, reason: str) -> bool:
    if device.status is to:
        return False
    db.add(
        DeviceStatusEvent(
            device_id=device.id, from_status=device.status, to_status=to, reason=reason
        )
    )
    frm = device.status
    device.status = to
    queue_event(
        db,
        device.facility_id,
        {"type": "device", "device_id": str(device.id), "status": to.value, "reason": reason},
    )
    if to is DeviceStatus.OFFLINE or (to is DeviceStatus.ONLINE and frm is DeviceStatus.OFFLINE):
        users, _ = notifications.resolve_recipients(
            db, device.facility_id, device.room, device.floor, CareTier.SUPERVISOR
        )
        verb = (
            "OFFLINE — this area is NOT monitored" if to is DeviceStatus.OFFLINE else "back online"
        )
        notifications.send(
            db,
            facility_id=device.facility_id,
            recipients=users,
            title=f"Camera {device.name} {verb}",
            body=f"{device.location or ''} Room {device.room or '-'} "
            f"Floor {device.floor or '-'}: {reason}",
            kind="device",
            tier=CareTier.SUPERVISOR.value,
            skip_already_notified=False,
        )
    return True


def record_heartbeat(
    db: Session, device: Device, hb: Heartbeat, now: datetime | None = None
) -> None:
    now = now or utcnow()
    device.last_seen = now
    device.firmware_version = hb.firmware_version
    device.model_version = hb.model_version
    device.last_uptime_seconds = hb.uptime_seconds
    device.last_rssi_dbm = hb.rssi_dbm
    device.last_temperature_c = hb.temperature_c
    device.last_power_state = hb.power_state
    device.last_free_psram_bytes = hb.free_psram_bytes
    device.buffer_effective_seconds = hb.buffer_effective_seconds
    device.reported_faults = hb.faults or []
    s = get_settings()
    if hb.faults:
        set_status(db, device, DeviceStatus.DEGRADED, "device fault: " + ", ".join(hb.faults))
    elif (
        hb.buffer_effective_seconds is not None
        and hb.buffer_effective_seconds < s.pre_event_seconds
    ):
        set_status(
            db,
            device,
            DeviceStatus.DEGRADED,
            f"buffer holds {hb.buffer_effective_seconds:.0f}s < "
            f"{s.pre_event_seconds}s pre-event window",
        )
    else:
        set_status(db, device, DeviceStatus.ONLINE, "heartbeat")


def sweep_health(db: Session, now: datetime | None = None) -> int:
    s = get_settings()
    now = now or utcnow()
    hb = s.device_heartbeat_seconds
    degraded_at = timedelta(seconds=hb * s.device_degraded_after_missed)
    offline_at = timedelta(seconds=hb * s.device_offline_after_missed)
    changed = 0
    devices = db.scalars(
        select(Device)
        .where(Device.status != DeviceStatus.UNPROVISIONED)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    for d in devices:
        if d.last_seen is None:
            continue
        age = now - d.last_seen
        if age >= offline_at:
            changed += set_status(
                db, d, DeviceStatus.OFFLINE, f"no heartbeat for {int(age.total_seconds())}s"
            )
        elif age >= degraded_at and d.status is DeviceStatus.ONLINE:
            changed += set_status(
                db, d, DeviceStatus.DEGRADED, f"heartbeat late by {int(age.total_seconds())}s"
            )
    return changed


def rotate_credentials(db: Session, device: Device, actor_id=None) -> str:
    secret = new_device_secret()
    device.credential_hash = hash_device_secret(secret)
    device.credential_rotated_at = utcnow()
    audit.record(
        db,
        "device.credentials_rotated",
        facility_id=device.facility_id,
        user_id=actor_id,
        target_type="device",
        target_id=device.id,
    )
    return secret


def device_config(device: Device) -> dict:
    """Configuration delivered to the device in every heartbeat response."""
    s = get_settings()
    return {
        "buffer_seconds": s.buffer_seconds,
        "pre_event_seconds": s.pre_event_seconds,
        "post_event_seconds": s.post_event_seconds,
        "heartbeat_seconds": s.device_heartbeat_seconds,
    }


def token_for(device_id, secret: str) -> str:
    return f"{device_id}.{secret}"
