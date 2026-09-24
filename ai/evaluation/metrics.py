"""Event-level fall-detection metrics.

Accuracy is meaningless here (falls are rare; "never alert" scores >99%). We report:

- recall (sensitivity): fraction of real falls alerted within the latency tolerance
- precision: fraction of alerts that correspond to a real fall
- F1
- false alarms per monitored hour — the operational number caregivers feel
- detection latency (alert time − fall onset) distribution

Matching: an alert matches a ground-truth fall if it lands within
[onset − early_tolerance, onset + max_latency]. Each fall matches at most one alert;
extra alerts for an already-matched fall count as duplicates (not false positives —
the backend groups them), and are reported separately.
"""

from dataclasses import dataclass, field
from statistics import median


@dataclass(frozen=True)
class Fall:
    video_id: str
    onset_s: float


@dataclass(frozen=True)
class Alert:
    video_id: str
    time_s: float


@dataclass
class Report:
    true_positives: int
    false_negatives: int
    false_positives: int
    duplicates: int
    monitored_hours: float
    latencies_s: list[float] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        n = self.true_positives + self.false_negatives
        return self.true_positives / n if n else None

    @property
    def precision(self) -> float | None:
        n = self.true_positives + self.false_positives
        return self.true_positives / n if n else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None
                                                      else None)

    @property
    def false_alarms_per_hour(self) -> float | None:
        return self.false_positives / self.monitored_hours if self.monitored_hours else None

    @property
    def median_latency_s(self) -> float | None:
        return median(self.latencies_s) if self.latencies_s else None

    def as_dict(self) -> dict:
        return {
            "tp": self.true_positives, "fn": self.false_negatives,
            "fp": self.false_positives, "duplicates": self.duplicates,
            "recall": self.recall, "precision": self.precision, "f1": self.f1,
            "false_alarms_per_hour": self.false_alarms_per_hour,
            "median_latency_s": self.median_latency_s,
            "monitored_hours": self.monitored_hours,
        }


def evaluate(falls: list[Fall], alerts: list[Alert], monitored_hours: float,
             max_latency_s: float = 30.0, early_tolerance_s: float = 2.0) -> Report:
    by_video: dict[str, list[Fall]] = {}
    for f in falls:
        by_video.setdefault(f.video_id, []).append(f)
    matched: set[Fall] = set()
    fp = dup = 0
    latencies: list[float] = []
    for a in sorted(alerts, key=lambda x: (x.video_id, x.time_s)):
        candidates = [f for f in by_video.get(a.video_id, [])
                      if f.onset_s - early_tolerance_s <= a.time_s <= f.onset_s + max_latency_s]
        if not candidates:
            fp += 1
            continue
        unmatched = [f for f in candidates if f not in matched]
        if not unmatched:
            dup += 1
            continue
        f = min(unmatched, key=lambda x: abs(a.time_s - x.onset_s))
        matched.add(f)
        latencies.append(max(0.0, a.time_s - f.onset_s))
    return Report(true_positives=len(matched), false_negatives=len(falls) - len(matched),
                  false_positives=fp, duplicates=dup, monitored_hours=monitored_hours,
                  latencies_s=latencies)
