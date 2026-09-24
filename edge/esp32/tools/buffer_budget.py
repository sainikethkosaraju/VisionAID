#!/usr/bin/env python3
"""Ephemeral-buffer memory budget for the DFR1154 (8 MB PSRAM).

    buffer_bytes ≈ fps × seconds × mean_jpeg_bytes × (1 + wrap_waste)

Frame sizes below are PLANNING ESTIMATES for OV3660 sensor-side JPEG. They must be
replaced with on-device measurements (docs/BUFFER_ARCHITECTURE.md §Measurement plan):
JPEG size depends on scene detail, IR night mode noise and quality setting.

Usage:
    python buffer_budget.py                       # default table
    python buffer_budget.py --measured HVGA=17.5  # override with measured mean KB
"""

import argparse

PSRAM_BYTES = 8 * 1024 * 1024
ARENA_BYTES = 4608 * 1024  # firmware ARENA_BYTES
WRAP_WASTE = 0.05  # variable-size frames waste the arena tail on each lap (bounded by 1 frame)

# (name, width, height, low_kb, high_kb) — planning range at jpeg_quality≈12-15
RESOLUTIONS = [
    ("QVGA", 320, 240, 5, 10),
    ("HVGA", 480, 320, 9, 18),
    ("VGA", 640, 480, 15, 30),
    ("SVGA", 800, 600, 25, 45),
]


def buffer_mb(fps: float, seconds: int, kb: float) -> float:
    return fps * seconds * kb * 1024 * (1 + WRAP_WASTE) / (1024 * 1024)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--fps", type=float, nargs="+", default=[2, 3, 4, 5, 8])
    ap.add_argument("--measured", nargs="*", default=[], help="NAME=mean_kb")
    args = ap.parse_args()
    measured = {k: float(v) for k, v in (m.split("=") for m in args.measured)}

    arena_mb = ARENA_BYTES / 2**20
    print(f"Arena budget: {arena_mb:.1f} MB of {PSRAM_BYTES / 2**20:.0f} MB PSRAM; "
          f"window {args.seconds}s\n")
    header = f"{'res':6} {'KB/frame':>10} " + " | ".join(f"{f:>12g}fps" for f in args.fps)
    print(header)
    for name, _w, _h, lo, hi in RESOLUTIONS:
        if name in measured:
            ranges, label = [(measured[name],)], f"{measured[name]:.1f}*"
        else:
            ranges, label = [(lo,), (hi,)], f"{lo}-{hi}"
        cells = []
        for fps in args.fps:
            vals = [buffer_mb(fps, args.seconds, r[0]) for r in ranges]
            worst = max(vals)
            mark = "ok" if worst <= arena_mb else ("~" if min(vals) <= arena_mb else "X")
            span = f"{min(vals):.1f}-{worst:.1f}" if len(vals) > 1 else f"{worst:.1f}"
            cells.append(f"{span}MB {mark}")
        print(f"{name:6} {label:>10} " + " | ".join(cells))
    print("\nok = fits worst case, ~ = fits only at low end, X = does not fit;"
          " * = measured")


if __name__ == "__main__":
    main()
