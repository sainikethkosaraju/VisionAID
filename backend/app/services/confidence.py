"""Confidence engine — heuristic fusion policy v0.

Combines the device's temporal trigger score, an optional server-side verifier score and
post-fall immobility into an incident confidence and a level.

The output is a *ranking signal for alerting*, not a calibrated or medical probability.
Thresholds are configuration and must be tuned on validation data (docs/AI_MODEL.md).

Levels:
  LOW        → keep monitoring (no incident record beyond SUSPICIOUS expiry)
  MEDIUM     → SUSPICIOUS
  HIGH       → POTENTIAL_FALL
  CONFIRMED  → HIGH + temporal descent evidence + post-fall immobility

Fail-safe: if the verifier is unavailable (not deployed, timed out) the engine falls back
to the device score alone. Degradation must bias toward alerting, never toward silence.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.config import Settings


class ConfidenceLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True)
class Observation:
    trigger_score: float  # 0..1 from device temporal trigger
    descent_detected: bool  # device saw rapid downward motion / orientation flip
    immobility_seconds: float = 0.0  # continuous low-motion time after the event
    verifier_score: float | None = None  # 0..1 from server model; None = not available


@dataclass(frozen=True)
class Assessment:
    confidence: float
    level: ConfidenceLevel
    reasons: tuple[str, ...]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def assess(obs: Observation, s: Settings) -> Assessment:
    reasons: list[str] = []
    trigger = _clamp(obs.trigger_score)
    if obs.verifier_score is None:
        score = trigger
        reasons.append("verifier unavailable: device score only")
    else:
        w = s.verifier_weight
        score = w * _clamp(obs.verifier_score) + (1.0 - w) * trigger
        reasons.append(f"fused verifier({obs.verifier_score:.2f})*{w} + trigger({trigger:.2f})")

    if score < s.conf_suspicious:
        return Assessment(score, ConfidenceLevel.LOW, tuple(reasons))
    if score < s.conf_potential:
        return Assessment(score, ConfidenceLevel.MEDIUM, tuple(reasons))

    if not obs.descent_detected:
        reasons.append("no temporal descent evidence")
        return Assessment(score, ConfidenceLevel.HIGH, tuple(reasons))
    if obs.immobility_seconds < s.immobility_confirm_seconds:
        reasons.append(
            f"immobility {obs.immobility_seconds:.0f}s < {s.immobility_confirm_seconds}s"
        )
        return Assessment(score, ConfidenceLevel.HIGH, tuple(reasons))

    reasons.append("descent + post-fall immobility")
    return Assessment(score, ConfidenceLevel.CONFIRMED, tuple(reasons))
