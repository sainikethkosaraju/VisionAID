"""Facility overview ("Is everyone safe?") and analytics."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ANY_STAFF, MANAGER
from app.core.timeutil import utcnow
from app.database.session import get_db
from app.models import Device, Facility, Incident, Resident, User
from app.models.enums import DeviceStatus, ResidentStatus
from app.schemas import FacilityOut, IncidentOut
from app.services import analytics
from app.services.state_machine import ACTIVE, ALERTING

router = APIRouter(tags=["facility"])


def _local_midnight(fac: Facility) -> datetime:
    tz = ZoneInfo(fac.timezone)
    now_local = utcnow().astimezone(tz)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/facility", response_model=FacilityOut)
def facility(user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)):
    return db.get(Facility, user.facility_id)


@router.get("/facility/overview")
def overview(user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)) -> dict:
    fac = db.get(Facility, user.facility_id)
    fid = user.facility_id
    device_counts = dict(
        db.execute(
            select(Device.status, func.count())
            .where(Device.facility_id == fid)
            .group_by(Device.status)
        ).all()
    )
    residents = db.scalar(
        select(func.count())
        .select_from(Resident)
        .where(Resident.facility_id == fid, Resident.status == ResidentStatus.ACTIVE)
    )
    active = db.scalars(
        select(Incident)
        .where(Incident.facility_id == fid, Incident.status.in_(list(ACTIVE)))
        .order_by(Incident.detected_at.desc())
    ).all()
    critical = [i for i in active if i.status in ALERTING]
    today = analytics.incident_metrics(db, fid, _local_midnight(fac), utcnow())

    online = device_counts.get(DeviceStatus.ONLINE, 0)
    degraded = device_counts.get(DeviceStatus.DEGRADED, 0)
    offline = device_counts.get(DeviceStatus.OFFLINE, 0)
    unprovisioned = device_counts.get(DeviceStatus.UNPROVISIONED, 0)
    total = online + degraded + offline + unprovisioned

    # All-clear is strict: no open incident (an acknowledged fall is still a person on the
    # floor) AND every provisioned camera reporting.
    in_hand = [i for i in active if i.status not in ALERTING]
    reasons = []
    if critical:
        reasons.append(f"{len(critical)} incident(s) awaiting response")
    if in_hand:
        reasons.append(f"{len(in_hand)} incident(s) open (being verified or handled)")
    if offline:
        reasons.append(f"{offline} camera(s) offline — areas unmonitored")
    if degraded:
        reasons.append(f"{degraded} camera(s) degraded")
    return {
        "all_clear": not reasons,
        "attention": reasons,
        "residents_monitored": residents,
        "devices": {
            "total": total,
            "online": online,
            "degraded": degraded,
            "offline": offline,
            "unprovisioned": unprovisioned,
        },
        "active_incidents": [IncidentOut.model_validate(i) for i in active],
        "critical_incident_count": len(critical),
        "today": {
            "alerts": today["counts"]["alerts"],
            "false_positives": today["counts"]["false_positives"],
            "avg_acknowledge_seconds": today["avg_acknowledge_seconds"],
            "avg_response_seconds": today["avg_response_seconds"],
        },
        "generated_at": utcnow(),
    }


@router.get("/analytics/incidents")
def incident_analytics(
    days: int = 30, user: User = Depends(MANAGER), db: Session = Depends(get_db)
) -> dict:
    if not 1 <= days <= 366:
        raise HTTPException(422, "days must be 1..366")
    end = utcnow()
    return analytics.incident_metrics(db, user.facility_id, end - timedelta(days=days), end)


@router.get("/analytics/devices")
def device_analytics(
    days: int = 30, user: User = Depends(MANAGER), db: Session = Depends(get_db)
) -> list[dict]:
    if not 1 <= days <= 366:
        raise HTTPException(422, "days must be 1..366")
    end = utcnow()
    return analytics.device_uptime(db, user.facility_id, end - timedelta(days=days), end)
