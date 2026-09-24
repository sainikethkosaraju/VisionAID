import pytest

from ai.evaluation.metrics import Alert, Fall, evaluate
from ai.preprocessing.splits import Clip, assign, check_no_leakage


def clips():
    out = []
    for subject in range(40):
        for take in range(6):
            out.append(Clip(f"c{subject}-{take}", "le2i", f"v{subject}-{take}",
                            f"s{subject}", "fall" if take % 2 else "walk"))
    for video in range(30):  # no subject ids: grouped by video
        out.append(Clip(f"o{video}", "oops", f"ov{video}", None, "fall"))
    return out


def test_group_split_has_no_subject_leakage():
    s = assign(clips())
    check_no_leakage(s)
    assert set(s) == {"train", "val", "test"}
    assert sum(len(v) for v in s.values()) == len(clips())


def test_split_is_deterministic_and_stable_when_data_grows():
    base = assign(clips())
    more = clips() + [Clip("new", "le2i", "vnew", "s999", "fall")]
    grown = assign(more)
    for split in base:
        assert {c.clip_id for c in base[split]} <= {c.clip_id for c in grown[split]}


def test_leakage_detector_fires():
    c = Clip("a", "d", "v", "s1", "fall")
    with pytest.raises(AssertionError):
        check_no_leakage({"train": [c], "test": [Clip("b", "d", "v2", "s1", "walk")]})


def test_event_metrics():
    falls = [Fall("v1", 10.0), Fall("v2", 50.0), Fall("v3", 5.0)]
    alerts = [Alert("v1", 14.0), Alert("v1", 16.0),  # match + duplicate
              Alert("v2", 200.0),                     # too late → FP, and v2 missed
              Alert("v4", 3.0)]                       # no fall → FP
    r = evaluate(falls, alerts, monitored_hours=2.0)
    assert (r.true_positives, r.false_negatives, r.false_positives, r.duplicates) == \
        (1, 2, 2, 1)
    assert r.recall == pytest.approx(1 / 3)
    assert r.precision == pytest.approx(1 / 3)
    assert r.false_alarms_per_hour == 1.0
    assert r.median_latency_s == 4.0


def test_metrics_with_no_alerts_is_zero_recall_not_error():
    r = evaluate([Fall("v", 1.0)], [], monitored_hours=1.0)
    assert r.recall == 0.0 and r.precision is None and r.f1 is None
