"""Facility analytics computed from recorded incidents and device status transitions.

Every figure is derived from stored data; nothing is synthesised. Aggregation happens in
Python over a bounded period — adequate at facility scale (hundreds of incidents/month).
Move to SQL/materialised views when a tenant exceeds ~100k incidents per query window.
"""

import uuid
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceStatusEvent, Facility, Incident, IncidentEvent
from app.models.enums import DeviceStatus
from app.models.enums import IncidentStatus as S


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def incident_metrics(db: Session, facility_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    fac = db.get(Facility, facility_id)
    tz = ZoneInfo(fac.timezone if fac else "UTC")
    incs = db.scalars(
        select(Incident).where(
            Incident.facility_id == facility_id,
            Incident.detected_at >= start,
            Incident.detected_at < end,
        )
    ).all()
    ids = [i.id for i in incs]
    reached_potential = set()
    if ids:
        reached_potential = set(
            db.scalars(
                select(IncidentEvent.incident_id).where(
                    IncidentEvent.incident_id.in_(ids), IncidentEvent.to_status == S.POTENTIAL_FALL
                )
            )
        )

    alerted = [i for i in incs if i.alert_sent_at is not None]
    fp = [i for i in alerted if i.status is S.FALSE_POSITIVE]
    confirmed_true = [i for i in alerted if i.status is not S.FALSE_POSITIVE]

    ack = [
        (i.acknowledged_at - i.alert_sent_at).total_seconds() for i in alerted if i.acknowledged_at
    ]
    resp = [(i.responding_at - i.alert_sent_at).total_seconds() for i in alerted if i.responding_at]
    resolve = [
        (i.resolved_at - i.alert_sent_at).total_seconds()
        for i in alerted
        if i.resolved_at and i.status is S.RESOLVED
    ]

    by_room = Counter(i.room or "unassigned" for i in alerted)
    by_floor = Counter(i.floor or "unassigned" for i in alerted)
    by_hour = Counter(i.detected_at.astimezone(tz).hour for i in alerted)
    repeat_rooms = {r: n for r, n in by_room.items() if n >= 2 and r != "unassigned"}
    repeat_residents = Counter(str(i.resident_id) for i in alerted if i.resident_id)

    bins = [0] * 10
    for i in incs:
        bins[min(int(i.confidence * 10), 9)] += 1

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": str(tz)},
        "counts": {
            "all_events": len(incs),
            "suspicious_only": sum(
                1 for i in incs if i.id not in reached_potential and i.alert_sent_at is None
            ),
            "potential_falls": len(reached_potential | {i.id for i in alerted}),
            "alerts": len(alerted),
            "confirmed_not_false_positive": len(confirmed_true),
            "false_positives": len(fp),
            "unresolved": sum(1 for i in incs if i.status is S.UNRESOLVED),
            "open": sum(
                1
                for i in alerted
                if i.status in (S.ALERT_SENT, S.ESCALATED, S.ACKNOWLEDGED, S.RESPONDING)
            ),
        },
        "false_positive_rate": round(len(fp) / len(alerted), 3) if alerted else None,
        "avg_acknowledge_seconds": _avg(ack),
        "avg_response_seconds": _avg(resp),
        "avg_resolution_seconds": _avg(resolve),
        "escalated_alerts": sum(1 for i in alerted if i.escalation_level > 0),
        "by_room": dict(by_room),
        "by_floor": dict(by_floor),
        "by_hour_local": {h: by_hour.get(h, 0) for h in range(24)},
        "repeat_rooms": repeat_rooms,
        "repeat_residents": {k: v for k, v in repeat_residents.items() if v >= 2},
        "confidence_histogram": {f"{b / 10:.1f}-{(b + 1) / 10:.1f}": n for b, n in enumerate(bins)},
    }


def device_uptime(
    db: Session, facility_id: uuid.UUID, start: datetime, end: datetime
) -> list[dict]:
    """Fraction of the window each device was ONLINE or DEGRADED (i.e. monitoring)."""
    out = []
    for d in db.scalars(select(Device).where(Device.facility_id == facility_id)):
        win_start = max(start, d.created_at)
        if win_start >= end:
            continue
        prior = db.scalar(
            select(DeviceStatusEvent)
            .where(DeviceStatusEvent.device_id == d.id, DeviceStatusEvent.at < win_start)
            .order_by(DeviceStatusEvent.at.desc())
            .limit(1)
        )
        status = prior.to_status if prior else DeviceStatus.UNPROVISIONED
        events = db.scalars(
            select(DeviceStatusEvent)
            .where(
                DeviceStatusEvent.device_id == d.id,
                DeviceStatusEvent.at >= win_start,
                DeviceStatusEvent.at < end,
            )
            .order_by(DeviceStatusEvent.at)
        ).all()
        up = timedelta()
        cursor = win_start
        for e in events:
            if status in (DeviceStatus.ONLINE, DeviceStatus.DEGRADED):
                up += e.at - cursor
            cursor, status = e.at, e.to_status
        if status in (DeviceStatus.ONLINE, DeviceStatus.DEGRADED):
            up += end - cursor
        total = (end - win_start).total_seconds()
        out.append(
            {
                "device_id": str(d.id),
                "name": d.name,
                "uptime_ratio": round(up.total_seconds() / total, 4) if total else None,
                "transitions": len(events),
            }
        )
    return out
