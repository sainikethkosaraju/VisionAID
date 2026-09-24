import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ANY_STAFF, CLINICAL, scoped
from app.database.session import get_db
from app.models import EvidenceClip, Incident, IncidentEvent, Notification, User
from app.models.enums import IncidentStatus
from app.schemas import (
    EvidenceOut,
    IncidentActionIn,
    IncidentDetailOut,
    IncidentEventOut,
    IncidentOut,
    NotificationOut,
)
from app.services import audit, evidence, incidents
from app.services.state_machine import ACTIVE, USER_ACTIONS, InvalidTransition

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    active: bool | None = None,
    status_: IncidentStatus | None = None,
    since: datetime | None = None,
    limit: int = 100,
    user: User = Depends(ANY_STAFF),
    db: Session = Depends(get_db),
):
    q = select(Incident).where(Incident.facility_id == user.facility_id)
    if active:
        q = q.where(Incident.status.in_(list(ACTIVE)))
    if status_:
        q = q.where(Incident.status == status_)
    if since:
        q = q.where(Incident.detected_at >= since)
    return db.scalars(q.order_by(Incident.detected_at.desc()).limit(min(limit, 500))).all()


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
def get_incident(
    incident_id: uuid.UUID, user: User = Depends(ANY_STAFF), db: Session = Depends(get_db)
):
    inc = scoped(db.get(Incident, incident_id), user, "incident")
    timeline = db.scalars(
        select(IncidentEvent).where(IncidentEvent.incident_id == inc.id).order_by(IncidentEvent.at)
    ).all()
    clips = db.scalars(
        select(EvidenceClip)
        .where(EvidenceClip.incident_id == inc.id)
        .order_by(EvidenceClip.first_frame_at)
    ).all()
    return IncidentDetailOut(
        **IncidentOut.model_validate(inc).model_dump(),
        timeline=[IncidentEventOut.model_validate(e) for e in timeline],
        evidence=[EvidenceOut.model_validate(c) for c in clips],
    )


@router.post("/incidents/{incident_id}/{action}", response_model=IncidentOut)
def act(
    incident_id: uuid.UUID,
    action: str,
    body: IncidentActionIn | None = None,
    user: User = Depends(ANY_STAFF),
    db: Session = Depends(get_db),
):
    target = USER_ACTIONS.get(action)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown action")
    inc = db.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    scoped(inc, user, "incident")
    try:
        incidents.user_transition(db, inc, target, user, note=body.note if body else None)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    audit.record(
        db,
        f"incident.{action}",
        facility_id=user.facility_id,
        user_id=user.id,
        target_type="incident",
        target_id=inc.id,
    )
    db.commit()
    return inc


@router.get("/evidence/{clip_id}/frames/{index}")
def evidence_frame(
    clip_id: uuid.UUID,
    index: int,
    request: Request,
    user: User = Depends(CLINICAL),
    db: Session = Depends(get_db),
):
    """Evidence access is restricted to clinical roles and every view is audited."""
    clip = db.get(EvidenceClip, clip_id)
    inc = db.get(Incident, clip.incident_id) if clip else None
    scoped(inc, user, "evidence")
    try:
        frames = evidence.load(clip)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_410_GONE, "evidence deleted by retention policy") from None
    except evidence.EvidenceUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    if not 0 <= index < len(frames):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "frame out of range")
    audit.record(
        db,
        "evidence.viewed",
        facility_id=user.facility_id,
        user_id=user.id,
        target_type="evidence_clip",
        target_id=clip_id,
        details={"frame": index},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return Response(
        frames[index].data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Frame-Captured-At": frames[index].at.isoformat()},
    )


@router.get("/notifications", response_model=list[NotificationOut])
def my_notifications(
    unacknowledged: bool = False,
    limit: int = 50,
    user: User = Depends(ANY_STAFF),
    db: Session = Depends(get_db),
):
    q = select(Notification).where(Notification.recipient_id == user.id)
    if unacknowledged:
        q = q.where(Notification.acknowledged_at.is_(None))
    return db.scalars(q.order_by(Notification.created_at.desc()).limit(min(limit, 200))).all()
