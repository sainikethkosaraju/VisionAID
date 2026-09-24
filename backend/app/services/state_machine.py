"""Incident state machine.

Pure transition rules — no I/O — so they can be exhaustively tested.

NORMAL is the observation state of a camera with no open incident; it is never stored
on an incident. An incident is born SUSPICIOUS or POTENTIAL_FALL.

Safety rule: once a caregiver could have been alerted, no state blocks a human response.
UNRESOLVED (ladder exhausted) can still be acknowledged, responded to and resolved.
"""

from app.models.enums import IncidentStatus as S

TERMINAL: frozenset[S] = frozenset({S.RESOLVED, S.FALSE_POSITIVE, S.CANCELLED})

# Statuses in which a caregiver has been (or is being) alerted and no one has responded.
ALERTING: frozenset[S] = frozenset({S.ALERT_SENT, S.ESCALATED, S.UNRESOLVED})

# Statuses that count as "active" on the home screen.
ACTIVE: frozenset[S] = frozenset(set(S) - TERMINAL - {S.SUSPICIOUS})

_HUMAN_RESPONSE = {S.ACKNOWLEDGED, S.RESPONDING, S.RESOLVED, S.FALSE_POSITIVE}

TRANSITIONS: dict[S, frozenset[S]] = {
    S.SUSPICIOUS: frozenset({S.POTENTIAL_FALL, S.CONFIRMED_FALL, S.CANCELLED, S.FALSE_POSITIVE}),
    S.POTENTIAL_FALL: frozenset({S.CONFIRMED_FALL, S.CANCELLED, S.FALSE_POSITIVE}),
    S.CONFIRMED_FALL: frozenset({S.ALERT_SENT, S.FALSE_POSITIVE}),
    S.ALERT_SENT: frozenset({S.ESCALATED, S.UNRESOLVED} | _HUMAN_RESPONSE),
    S.ESCALATED: frozenset({S.ESCALATED, S.UNRESOLVED} | _HUMAN_RESPONSE),
    S.ACKNOWLEDGED: frozenset({S.RESPONDING, S.RESOLVED, S.FALSE_POSITIVE, S.UNRESOLVED}),
    S.RESPONDING: frozenset({S.RESOLVED, S.FALSE_POSITIVE, S.UNRESOLVED}),
    S.UNRESOLVED: frozenset(_HUMAN_RESPONSE),
    S.RESOLVED: frozenset(),
    S.FALSE_POSITIVE: frozenset(),
    S.CANCELLED: frozenset(),
}

# Transitions a caregiver may request through the API. System-only transitions
# (confirmation, alerting, escalation, auto-cancel) are not user-reachable.
USER_ACTIONS: dict[str, S] = {
    "acknowledge": S.ACKNOWLEDGED,
    "respond": S.RESPONDING,
    "resolve": S.RESOLVED,
    "false_positive": S.FALSE_POSITIVE,
}


class InvalidTransition(ValueError):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"cannot transition incident from {current.value} to {target.value}")
        self.current = current
        self.target = target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def check_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
