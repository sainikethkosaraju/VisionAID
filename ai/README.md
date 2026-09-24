# AI

| Path | Status |
|---|---|
| `datasets/registry.yaml` | Dataset registry with licence gating |
| `preprocessing/splits.py` | Leak-proof subject/video-grouped splits — tested |
| `evaluation/metrics.py` | Event-level recall/precision/F1, false alarms/hour, latency — tested |
| `training/`, `inference/`, `export/`, `models/` | PENDING (Phase 4) |

Design: [docs/AI_MODEL.md](../docs/AI_MODEL.md) · data: [docs/DATASET.md](../docs/DATASET.md).
Run tests from the repo root: `python -m pytest ai/tests`.
