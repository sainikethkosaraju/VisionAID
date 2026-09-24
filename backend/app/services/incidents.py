"""Incident lifecycle: ingest, grouping, promotion, alerting, escalation, user actions.

Invariants
- Automatic logic only ever *promotes* an incident toward alerting. Once a caregiver has
  been alerted, only a human can close it (a late low verifier score is recorded, not
  acted on).
- Every transition is written to incident_events in the same transaction.
- A new trigger on a device with an open incident is grouped into it (no alert storm);
  a trigger on an idle device always creates a new incident — past false positives
  never suppress a future alert.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.timeutil import utcnow
from app.models import Device, Incident, IncidentEvent, Resident, User
from app.models.enums import CareTier, Priority, ResidentStatus
from app.models.enums import IncidentStatus as S
from app.services import notifications
from app.services.confidence import Assessment, ConfidenceLevel, Observation, assess
from app.services.realtime import queue_event
from app.services.state_machine import ALERTING, TERMINAL, check_transition


@dataclass
class TriggerInput:
    event_id: str
    occurred_at: datetime
    trigger_score: float
    descent_detected: bool
    immobility_seconds: float = 0.0
    recovered: bool = False
    features: dict | None = None


@dataclass
class ObservationInput:
    immobility_seconds: float | None = None
    recovered: bool | None = None
    trigger_score: float | None = None
    verifier_score: float | None = None


def _event(
    db: Session,
    inc: Incident,
    kind: str,
    *,
    frm: S | None = None,
    to: S | None = None,
    actor: str = "system",
    user: User | None = None,
    note: str | None = None,
    data: dict | None = None,
) -> None:
    db.add(
        IncidentEvent(
            incident_id=inc.id,
            kind=kind,
            from_status=frm,
            to_status=to,
            actor=actor,
            actor_user_id=user.id if user else None,
            note=note,
            data=data or {},
        )
    )


def _publish(db: Session, inc: Incident) -> None:
    queue_event(
        db,
        inc.facility_id,
        {
            "type": "incident",
            "incident_id": str(inc.id),
            "status": inc.status.value,
            "priority": inc.priority.value,
            "room": inc.room,
            "floor": inc.floor,
            "confidence": round(inc.confidence, 3),
            "escalation_level": inc.escalation_level,
        },
    )


def _move(
    db: Session,
    inc: Incident,
    to: S,
    *,
    actor: str = "system",
    user: User | None = None,
    note: str | None = None,
) -> None:
    check_transition(inc.status, to)
    frm = inc.status
    inc.status = to
    _event(db, inc, "transition", frm=frm, to=to, actor=actor, user=user, note=note)


def _observation(inc: Incident, verifier: float | None = None) -> Observation:
    return Observation(
        trigger_score=inc.trigger_score,
        descent_detected=bool(inc.features.get("descent_detected")),
        immobility_seconds=inc.immobility_seconds,
        verifier_score=inc.verifier_score if verifier is None else verifier,
    )


def _apply_assessment(
    db: Session, inc: Incident, a: Assessment, s: Settings, now: datetime
) -> None:
    inc.confidence = a.confidence
    inc.confidence_level = a.level.value
    if inc.status in TERMINAL or inc.status not in (S.SUSPICIOUS, S.POTENTIAL_FALL):
        return  # already confirmed/alerting or closed: never demote automatically
    if a.level is ConfidenceLevel.HIGH and inc.status is S.SUSPICIOUS:
        _move(db, inc, S.POTENTIAL_FALL, note="; ".join(a.reasons))
        inc.priority = Priority.HIGH
    if a.level is ConfidenceLevel.CONFIRMED:
        confirm_and_alert(db, inc, s, now, reason="; ".join(a.reasons))


def _escalation_steps(db: Session, inc: Incident, s: Settings) -> list[tuple[int, str]]:
    from app.core.config import parse_escalation
    from app.models import Facility

    fac = db.get(Facility, inc.facility_id)
    override = (fac.settings or {}).get("escalation_policy") if fac else None
    return parse_escalation(override) if override else s.escalation_steps


def confirm_and_alert(db: Session, inc: Incident, s: Settings, now: datetime, reason: str) -> None:
    if inc.status in (S.SUSPICIOUS, S.POTENTIAL_FALL):
        _move(db, inc, S.CONFIRMED_FALL, note=reason)
        inc.confirmed_at = now
    if inc.status is not S.CONFIRMED_FALL:
        return
    inc.priority = Priority.CRITICAL
    steps = _escalation_steps(db, inc, s)
    tier = CareTier(steps[0][1])
    _notify_tier(db, inc, tier)
    _move(db, inc, S.ALERT_SENT, note=f"notified {tier.value}")
    inc.alert_sent_at = now
    inc.escalation_level = 0
    inc.next_escalation_at = _next_escalation(inc, steps, s)


def _next_escalation(inc: Incident, steps: list[tuple[int, str]], s: Settings) -> datetime:
    assert inc.alert_sent_at is not None
    nxt = inc.escalation_level + 1
    if nxt < len(steps):
        return inc.alert_sent_at + timedelta(seconds=steps[nxt][0])
    final = max(s.unresolved_after_seconds, steps[-1][0])
    return inc.alert_sent_at + timedelta(seconds=final)


def _describe(inc: Incident) -> tuple[str, str]:
    where = ", ".join(
        p
        for p in (
            f"Room {inc.room}" if inc.room else None,
            f"Floor {inc.floor}" if inc.floor else None,
        )
        if p
    )
    title = f"Possible fall — {where or 'unassigned location'}"
    body = (
        f"Detected {inc.detected_at:%H:%M:%S} UTC. Engine confidence "
        f"{inc.confidence:.0%} (alerting score, not a medical probability)."
    )
    return title, body


def _notify_tier(db: Session, inc: Incident, tier: CareTier) -> None:
    users, fallback = notifications.resolve_recipients(
        db, inc.facility_id, inc.room, inc.floor, tier
    )
    title, body = _describe(inc)
    if tier is not CareTier.PRIMARY:
        title = f"[ESCALATED · {tier.value}] {title}"
    sent = notifications.send(
        db,
        facility_id=inc.facility_id,
        recipients=users,
        title=title,
        body=body,
        incident=inc,
        tier=tier.value,
    )
    data = {
        "tier": tier.value,
        "recipients": [str(u.id) for u in users],
        "notifications": len(sent),
        "fallback": fallback,
    }
    note = None
    if fallback:
        note = f"no {tier.value} assigned for this location — fell back to supervisors/admins"
    if not users:
        note = "COVERAGE GAP: no eligible recipient on duty"
    _event(db, inc, "escalation", note=note, data=data)


def _match_resident(db: Session, device: Device) -> uuid.UUID | None:
    if not device.room:
        return None
    ids = list(
        db.scalars(
            select(Resident.id).where(
                Resident.facility_id == device.facility_id,
                Resident.room == device.room,
                Resident.status == ResidentStatus.ACTIVE,
            )
        )
    )
    return ids[0] if len(ids) == 1 else None  # shared room: do not guess


def ingest_trigger(
    db: Session, device: Device, t: TriggerInput, now: datetime | None = None
) -> tuple[Incident, bool]:
    """Returns (incident, created). Idempotent on (device, event_id)."""
    s = get_settings()
    now = now or utcnow()

    existing = db.scalar(
        select(Incident).where(
            Incident.device_id == device.id, Incident.device_event_id == t.event_id
        )
    )
    if existing:
        return existing, False

    window_start = now - timedelta(seconds=s.incident_group_seconds)
    open_inc = db.scalar(
        select(Incident)
        .where(
            Incident.device_id == device.id,
            Incident.status.not_in(list(TERMINAL)),
            Incident.detected_at >= window_start,
        )
        .order_by(Incident.detected_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if open_inc is not None:
        open_inc.trigger_count += 1
        open_inc.trigger_score = max(open_inc.trigger_score, t.trigger_score)
        open_inc.immobility_seconds = max(open_inc.immobility_seconds, t.immobility_seconds)
        if t.descent_detected:
            open_inc.features = {**open_inc.features, "descent_detected": True}
        _event(
            db,
            open_inc,
            "observation",
            actor="device",
            note="grouped re-trigger",
            data={"event_id": t.event_id, "trigger_score": t.trigger_score},
        )
        _apply_assessment(db, open_inc, assess(_observation(open_inc), s), s, now)
        _publish(db, open_inc)
        return open_inc, False

    a = assess(Observation(t.trigger_score, t.descent_detected, t.immobility_seconds), s)
    status = (
        S.POTENTIAL_FALL
        if a.level in (ConfidenceLevel.HIGH, ConfidenceLevel.CONFIRMED)
        else S.SUSPICIOUS
    )
    inc = Incident(
        facility_id=device.facility_id,
        device_id=device.id,
        device_event_id=t.event_id,
        resident_id=_match_resident(db, device),
        status=status,
        priority=Priority.HIGH if status is S.POTENTIAL_FALL else Priority.LOW,
        room=device.room,
        floor=device.floor,
        detected_at=t.occurred_at,
        confidence=a.confidence,
        confidence_level=a.level.value,
        trigger_score=t.trigger_score,
        immobility_seconds=t.immobility_seconds,
        features={
            **(t.features or {}),
            "descent_detected": t.descent_detected,
            "recovered": t.recovered,
        },
    )
    db.add(inc)
    db.flush()
    _event(
        db,
        inc,
        "transition",
        to=status,
        actor="device",
        note="; ".join(a.reasons),
        data={"trigger_score": t.trigger_score},
    )
    if a.level is ConfidenceLevel.CONFIRMED:
        confirm_and_alert(db, inc, s, now, reason="; ".join(a.reasons))
    _publish(db, inc)
    return inc, True


def observe(
    db: Session,
    inc: Incident,
    o: ObservationInput,
    actor: str = "device",
    now: datetime | None = None,
) -> Incident:
    s = get_settings()
    now = now or utcnow()
    data = {k: v for k, v in o.__dict__.items() if v is not None}
    if o.immobility_seconds is not None:
        inc.immobility_seconds = max(inc.immobility_seconds, o.immobility_seconds)
    if o.trigger_score is not None:
        inc.trigger_score = max(inc.trigger_score, o.trigger_score)
    if o.verifier_score is not None:
        inc.verifier_score = o.verifier_score
    if o.recovered is not None:
        inc.features = {**inc.features, "recovered": o.recovered}
    note = None
    if inc.status in ALERTING | {S.ACKNOWLEDGED, S.RESPONDING}:
        note = "recorded only: alert already raised, a human must close this incident"
    _event(db, inc, "observation", actor=actor, note=note, data=data)
    _apply_assessment(db, inc, assess(_observation(inc), s), s, now)
    _publish(db, inc)
    return inc


def user_transition(
    db: Session,
    inc: Incident,
    target: S,
    user: User,
    note: str | None = None,
    now: datetime | None = None,
) -> Incident:
    now = now or utcnow()
    _move(db, inc, target, actor="user", user=user, note=note)
    if target is S.ACKNOWLEDGED:
        inc.acknowledged_at, inc.acknowledged_by = now, user.id
    elif target is S.RESPONDING:
        inc.responding_at, inc.responding_by = now, user.id
        if inc.acknowledged_at is None:
            inc.acknowledged_at, inc.acknowledged_by = now, user.id
    elif target in (S.RESOLVED, S.FALSE_POSITIVE):
        inc.resolved_at, inc.resolved_by = now, user.id
        inc.resolution_note = note
    inc.next_escalation_at = None  # any human response stops the escalation ladder
    notifications.acknowledge_for_incident(db, inc.id, user.id)
    _publish(db, inc)
    return inc


# --- Periodic work (driven by app.workers.scheduler) ---------------------------------


def run_escalation_cycle(db: Session, now: datetime | None = None) -> int:
    s = get_settings()
    now = now or utcnow()
    due = db.scalars(
        select(Incident)
        .where(Incident.status.in_([S.ALERT_SENT, S.ESCALATED]), Incident.next_escalation_at <= now)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for inc in due:
        steps = _escalation_steps(db, inc, s)
        nxt = inc.escalation_level + 1
        if nxt < len(steps):
            tier = CareTier(steps[nxt][1])
            _notify_tier(db, inc, tier)
            _move(db, inc, S.ESCALATED, note=f"no acknowledgement — escalated to {tier.value}")
            inc.escalation_level = nxt
            inc.next_escalation_at = _next_escalation(inc, steps, s)
        else:
            _move(db, inc, S.UNRESOLVED, note="escalation ladder exhausted without response")
            inc.next_escalation_at = None
            users, _ = notifications.resolve_recipients(
                db, inc.facility_id, inc.room, inc.floor, CareTier.SUPERVISOR
            )
            title, body = _describe(inc)
            notifications.send(
                db,
                facility_id=inc.facility_id,
                recipients=users,
                title=f"[UNRESOLVED] {title}",
                body=body,
                incident=inc,
                tier="UNRESOLVED",
                skip_already_notified=False,
            )
        _publish(db, inc)
    return len(due)


def run_expiry_cycle(db: Session, now: datetime | None = None) -> int:
    """Close stale SUSPICIOUS incidents; promote un-recovered POTENTIAL_FALLs."""
    s = get_settings()
    now = now or utcnow()
    count = 0
    stale_suspicious = db.scalars(
        select(Incident)
        .where(
            Incident.status == S.SUSPICIOUS,
            Incident.detected_at <= now - timedelta(seconds=s.suspicious_expiry_seconds),
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for inc in stale_suspicious:
        _move(db, inc, S.CANCELLED, note="expired below alert threshold")
        _publish(db, inc)
        count += 1

    stale_potential = db.scalars(
        select(Incident)
        .where(
            Incident.status == S.POTENTIAL_FALL,
            Incident.detected_at <= now - timedelta(seconds=s.potential_fall_timeout_seconds),
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for inc in stale_potential:
        if inc.features.get("recovered"):
            _move(db, inc, S.CANCELLED, note="person observed recovering; no alert")
        else:
            confirm_and_alert(
                db,
                inc,
                s,
                now,
                reason=f"no recovery observed within {s.potential_fall_timeout_seconds}s",
            )
        _publish(db, inc)
        count += 1
    return count
