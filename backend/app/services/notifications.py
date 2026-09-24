"""Care-network resolution and notification dispatch.

Who is responsible for this location? For a tier, the most specific active assignment
wins: room → floor → facility-wide. If a tier has nobody, we fall back to on-duty
supervisors/admins and record the coverage gap — an alert must never go to nobody.
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models import CareAssignment, Incident, Notification, User
from app.models.enums import (
    CareTier,
    NotificationChannel,
    NotificationStatus,
    UserRole,
    UserStatus,
)
from app.services.realtime import queue_event

log = logging.getLogger(__name__)

# External channel providers register here. Until one is registered the channel is
# PENDING INTEGRATION and attempts are recorded as FAILED rather than silently dropped.
Provider = Callable[[Notification, User], None]
PROVIDERS: dict[NotificationChannel, Provider] = {}


def _eligible(q):
    return q.where(User.status == UserStatus.ACTIVE, User.on_duty.is_(True))


def resolve_recipients(
    db: Session, facility_id: uuid.UUID, room: str | None, floor: str | None, tier: CareTier
) -> tuple[list[User], bool]:
    """Return (users, is_fallback)."""
    base = _eligible(
        select(User)
        .join(CareAssignment, CareAssignment.user_id == User.id)
        .where(
            CareAssignment.facility_id == facility_id,
            CareAssignment.active.is_(True),
            CareAssignment.tier == tier,
        )
    )
    scopes = []
    if room is not None:
        scopes.append(
            and_(
                CareAssignment.room == room,
                or_(CareAssignment.floor == floor, CareAssignment.floor.is_(None)),
            )
        )
    if floor is not None:
        scopes.append(and_(CareAssignment.floor == floor, CareAssignment.room.is_(None)))
    scopes.append(and_(CareAssignment.floor.is_(None), CareAssignment.room.is_(None)))

    for scope in scopes:
        users = list(db.scalars(base.where(scope)).unique())
        if users:
            return users, False

    fallback = list(
        db.scalars(
            _eligible(
                select(User).where(
                    User.facility_id == facility_id,
                    User.role.in_([UserRole.SUPERVISOR, UserRole.ADMIN]),
                )
            )
        )
    )
    return fallback, True


def _channels() -> list[NotificationChannel]:
    out = []
    for name in get_settings().notification_channels:
        try:
            out.append(NotificationChannel(name))
        except ValueError:
            log.error("unknown notification channel configured: %s", name)
    return out or [NotificationChannel.IN_APP]


def send(
    db: Session,
    *,
    facility_id: uuid.UUID,
    recipients: list[User],
    title: str,
    body: str,
    incident: Incident | None = None,
    tier: str | None = None,
    kind: str = "incident",
    skip_already_notified: bool = True,
) -> list[Notification]:
    now = utcnow()
    already: set[uuid.UUID] = set()
    if incident is not None and skip_already_notified:
        already = set(
            db.scalars(
                select(Notification.recipient_id).where(Notification.incident_id == incident.id)
            )
        )

    created: list[Notification] = []
    for user in recipients:
        if user.id in already:
            continue
        for channel in _channels():
            n = Notification(
                incident_id=incident.id if incident else None,
                recipient_id=user.id,
                channel=channel,
                tier=tier,
                kind=kind,
                title=title,
                body=body,
            )
            if channel is NotificationChannel.IN_APP:
                n.status, n.sent_at = NotificationStatus.SENT, now
            elif channel in PROVIDERS:
                try:
                    PROVIDERS[channel](n, user)
                    n.status, n.sent_at = NotificationStatus.SENT, now
                except Exception as exc:  # noqa: BLE001
                    n.status, n.error = NotificationStatus.FAILED, str(exc)[:500]
            else:
                n.status = NotificationStatus.FAILED
                n.error = f"PENDING INTEGRATION: no provider for channel '{channel.value}'"
            db.add(n)
            created.append(n)

    db.flush()
    for n in created:
        if n.channel is NotificationChannel.IN_APP:
            queue_event(
                db,
                facility_id,
                {
                    "type": "notification",
                    "recipient_id": str(n.recipient_id),
                    "notification_id": str(n.id),
                    "incident_id": str(n.incident_id) if n.incident_id else None,
                    "title": n.title,
                    "body": n.body,
                    "tier": n.tier,
                    "kind": n.kind,
                },
            )
    return created


def acknowledge_for_incident(db: Session, incident_id: uuid.UUID, user_id: uuid.UUID) -> None:
    now = utcnow()
    for n in db.scalars(select(Notification).where(Notification.incident_id == incident_id)):
        if n.recipient_id == user_id and n.status in (
            NotificationStatus.SENT,
            NotificationStatus.DELIVERED,
        ):
            n.status, n.acknowledged_at = NotificationStatus.ACKNOWLEDGED, now
