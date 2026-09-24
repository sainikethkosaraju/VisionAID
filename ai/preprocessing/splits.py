"""Leak-proof dataset splits.

Frames from the same person (or the same recording) must never appear in more than one
split: a model can memorise a subject's clothing, body and room and report inflated
accuracy. Splits are assigned per GROUP (subject, else video) deterministically by hash,
so adding new data never reshuffles existing assignments.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Clip:
    clip_id: str
    dataset: str
    video_id: str
    subject_id: str | None
    label: str


def group_key(c: Clip) -> str:
    # Subjects are only unique within a dataset.
    return f"{c.dataset}:{'s:' + c.subject_id if c.subject_id else 'v:' + c.video_id}"


def _bucket(key: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}:{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def assign(clips: Iterable[Clip], val: float = 0.15, test: float = 0.15,
           salt: str = "visionaid-v1") -> dict[str, list[Clip]]:
    if not 0 < val + test < 1:
        raise ValueError("val + test must be in (0, 1)")
    out: dict[str, list[Clip]] = defaultdict(list)
    for c in clips:
        b = _bucket(group_key(c), salt)
        split = "test" if b < test else "val" if b < test + val else "train"
        out[split].append(c)
    return dict(out)


def check_no_leakage(splits: dict[str, list[Clip]]) -> None:
    seen: dict[str, str] = {}
    for split, clips in splits.items():
        for c in clips:
            g = group_key(c)
            if seen.setdefault(g, split) != split:
                raise AssertionError(f"group {g} appears in {seen[g]} and {split}")
