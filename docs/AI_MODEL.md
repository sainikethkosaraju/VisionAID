# AI Model Architecture

> Status: on-device **trigger heuristic v0 implemented and host-tested on synthetic trajectories**. Person detector integration and server verifier: **PENDING**. No model has been trained or validated on real footage. No accuracy claim is made anywhere in this repository.

## 1. Two-stage design (why)

| Stage | Runs on | Goal | Model |
|---|---|---|---|
| 1. Trigger | ESP32-S3 | **Recall** — never miss a candidate fall | ESP-DL pedestrian detector (bbox, ~130 ms) + temporal FSM `fall_trigger` |
| 2. Verifier | Backend | **Precision** — reject non-falls | Pose keypoints + lightweight temporal classifier over the 30 s pre-event window |
| Fusion | Backend | Decide + explain | Confidence engine (`backend/app/services/confidence.py`) |

Pose on-device is infeasible (YOLO11n-pose ≈ 27.8 s/frame on ESP32-S3, Espressif benchmark). Person detection at ~7 Hz is feasible.

## 2. Stage 1 — temporal fall trigger (heuristic v0)

`edge/esp32/components/fall_trigger`. Per inference tick it consumes one bbox and scores the last 1.5 s:

| Feature | Why it separates falls | Weight |
|---|---|---|
| Downward speed of the bbox **top edge**, in upright-body-heights/s | Falls are uncontrolled: fast. Lying on a bed/sitting is slow. | 0.5 |
| Height collapse `1 − h/h_upright` | Body ends near the floor | 0.3 |
| Aspect flip (upright → horizontal) | Orientation change | 0.2 |

A trigger requires **both** speed ≥ `velocity_lo` **and** collapse ≥ `height_drop_lo` — so "person lying down" alone never triggers (spec §8). After a trigger: **immobility** accumulates only while the person is visible and still; **recovery** requires ≥ 2 s upright; **occlusion is neither** (it must not look like recovery).

Host-tested scenarios: fast fall → trigger + immobility; standing, walking, intentional lying on a bed (4 s), fast sit into a chair, bending → no trigger; fall then getting up → RECOVERED; fall then occluded → no recovery, no immobility; 3 Hz sampling still triggers.

**Limitations, stated plainly:** thresholds are physically motivated starting points, not fitted; synthetic trajectories prove logic, not real-world accuracy; fisheye distortion, camera height and occlusion are unmodelled; slow "sliding" falls (e.g. out of a chair) may not reach the speed threshold — a known false-negative class to measure.

## 3. Stage 2 — server verifier (candidates evaluated)

| Candidate | Pros | Cons | Verdict |
|---|---|---|---|
| Frame classifier (MobileNet/EfficientNet-Lite "lying vs not") | Simple | No temporal reasoning → "lying down = fall" — explicitly rejected by spec | ✗ |
| YOLO detect + heuristics | Fast | Same limits as stage 1 | ✗ (that is stage 1) |
| CNN + LSTM/GRU on frames | Temporal | Appearance-heavy, data-hungry, overfits to rooms/clothing | Backup |
| Lightweight video nets (X3D-XS, MoViNet-A0) | Strong temporal features | Needs lots of labelled video; heavier; appearance bias | Backup |
| **Pose keypoints (YOLO-pose / RTMPose) → temporal model (GRU or TCN / ST-GCN) over 2–4 s** | Appearance-invariant; data-efficient; interpretable (hip/head trajectories); keypoints are privacy-friendlier than pixels | Pose degrades with fisheye + occlusion; two models to maintain | **Recommended** |

Recommendation is provisional: the deciding evidence is recall/false-alarm on **our** camera geometry (DATASET.md), and the evaluation harness is built to compare candidates on identical, leak-free splits.

## 4. Confidence engine

Implemented in `backend/app/services/confidence.py`. Levels: LOW → keep monitoring; MEDIUM → SUSPICIOUS; HIGH → POTENTIAL_FALL; HIGH + descent + immobility ≥ `IMMOBILITY_CONFIRM_SECONDS` → CONFIRMED.

- With a verifier score: `confidence = w·verifier + (1−w)·trigger` (w = 0.7).
- Without one: device score alone, and the reason says so.
- Automatic logic **only promotes**. A late low verifier score after an alert is recorded, never used to cancel.
- POTENTIAL_FALL with no observed recovery for `POTENTIAL_FALL_TIMEOUT_SECONDS` (45 s) → alert. Covers people who fall and keep moving but cannot rise.
- The value is an alerting score, **not a medical probability**, and notifications say so.

All thresholds are environment configuration.

## 5. Training pipeline (Phase 4)

```
registry (licence-gated) → ingest → validation (corrupt/short clips, label sanity)
  → subject/video-grouped split (ai/preprocessing/splits.py — leak-proof, deterministic)
  → preprocessing (resize to camera geometry; optional fisheye warp augmentation)
  → augmentation (IR/greyscale, blur, JPEG q, exposure, horizontal flip, occluders)
  → pose extraction (cached) → temporal model training
  → validation (threshold selection on VAL only) → TEST (once, frozen)
  → INT8 quantisation (verifier: ONNX/TensorRT; detector: ESP-PPQ → .espdl)
  → export + model card → edge benchmark (latency, PSRAM, flash) on DFR1154
```

Tracked for every run: precision, recall, F1, false positives, false negatives, **false alarms per monitored hour**, detection latency (median/p95), mAP where a detector is trained, model size, RAM/PSRAM, latency and FPS on target. `ai/evaluation/metrics.py` implements event-level matching (one alert per fall, duplicates counted separately) — never frame accuracy.

## 6. Proposed acceptance targets (to validate, not claims)

| Metric | Target for pilot entry |
|---|---|
| Recall on held-out staged falls (our camera) | ≥ 95% |
| False alarms reaching caregivers | ≤ 1 per camera per week |
| Alert latency (fall onset → notification), p95 | ≤ 20 s (dominated by immobility confirmation) |

These are product targets for discussion with pilot sites, derived from alert-fatigue concerns, not from literature benchmarks.
