"""Pure-logic tests: state machine, confidence engine, configuration validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings, parse_escalation
from app.models.enums import IncidentStatus as S
from app.services.confidence import ConfidenceLevel, Observation, assess
from app.services.state_machine import (
    TERMINAL,
    TRANSITIONS,
    USER_ACTIONS,
    InvalidTransition,
    can_transition,
    check_transition,
)


def test_every_status_has_rules_and_terminals_are_final():
    assert set(TRANSITIONS) == set(S)
    for t in TERMINAL:
        assert TRANSITIONS[t] == frozenset()


def test_every_status_reachable_from_entry_states():
    seen, frontier = set(), [S.SUSPICIOUS, S.POTENTIAL_FALL]
    while frontier:
        s = frontier.pop()
        if s in seen:
            continue
        seen.add(s)
        frontier.extend(TRANSITIONS[s])
    assert seen == set(S)


@pytest.mark.parametrize("state", [S.ALERT_SENT, S.ESCALATED, S.UNRESOLVED])
def test_alerted_incident_always_accepts_every_human_response(state):
    for target in USER_ACTIONS.values():
        assert can_transition(state, target), (state, target)


def test_cannot_skip_confirmation_or_reopen():
    with pytest.raises(InvalidTransition):
        check_transition(S.POTENTIAL_FALL, S.ALERT_SENT)
    with pytest.raises(InvalidTransition):
        check_transition(S.RESOLVED, S.ACKNOWLEDGED)
    with pytest.raises(InvalidTransition):
        check_transition(S.SUSPICIOUS, S.ACKNOWLEDGED)


s = get_settings()


def test_confidence_levels():
    assert assess(Observation(0.1, True, 60), s).level is ConfidenceLevel.LOW
    assert assess(Observation(0.5, True, 60), s).level is ConfidenceLevel.MEDIUM
    assert assess(Observation(0.8, False, 60), s).level is ConfidenceLevel.HIGH
    assert assess(Observation(0.8, True, 3), s).level is ConfidenceLevel.HIGH
    assert assess(Observation(0.8, True, 12), s).level is ConfidenceLevel.CONFIRMED


def test_verifier_fusion_and_fallback():
    no_verifier = assess(Observation(0.9, True, 30), s)
    assert no_verifier.confidence == pytest.approx(0.9)
    assert "verifier unavailable" in no_verifier.reasons[0]
    vetoed = assess(Observation(0.9, True, 30, verifier_score=0.05), s)
    assert vetoed.level is ConfidenceLevel.LOW
    assert vetoed.confidence == pytest.approx(0.7 * 0.05 + 0.3 * 0.9)


def test_scores_are_clamped():
    assert assess(Observation(7.0, True, 30), s).confidence == 1.0


def test_config_rejects_inconsistent_windows():
    with pytest.raises(ValidationError):
        Settings(jwt_secret="x", buffer_seconds=30, pre_event_seconds=45)
    with pytest.raises(ValidationError):
        Settings(jwt_secret="x", conf_suspicious=0.7, conf_potential=0.6)
    with pytest.raises(ValidationError):
        Settings(jwt_secret="short", env="production")


def test_escalation_policy_parsing():
    assert parse_escalation("30:secondary, 0:primary") == [(0, "PRIMARY"), (30, "SECONDARY")]
    with pytest.raises(ValueError):
        parse_escalation("10:primary")
