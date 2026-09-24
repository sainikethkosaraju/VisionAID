# Dataset Strategy

Machine-readable registry: [`ai/datasets/registry.yaml`](../ai/datasets/registry.yaml).

## 1. Candidates

Figures from the OmniFall benchmark paper ([arXiv:2505.19889](https://arxiv.org/abs/2505.19889)); to be re-verified against each source publication.

| Dataset | Duration | Subjects | Views | Real elderly? | Licence status |
|---|---|---|---|---|---|
| CMDFall | 7h25m | 50 | 7 sync | No (staged) | unverified |
| UP-Fall | 4h37m | 17 | 2 sync | No | unverified |
| Le2i | 51m | 9 | 6 rooms | No | unverified |
| GMDCSA-24 | 21m | 4 | 3 homes | No | unverified |
| EDF / OCCU / MCFD | 14m / 14m / 18m | 5 / 5 / 1 | 2 / 2 / 8 | No | unverified |
| CAUCAFall | 16m | 10 | 1 | No | unverified |
| OOPS-Fall | 1.3k segments | many | in-the-wild | No (real, unstaged falls) | unverified |
| OmniFall labels | — | — | — | — | CC BY-SA 4.0 (labels only) |
| "Older Adults Falls Dataset" | — | — | — | — | **not identified** — source URL needed |

Findings:
1. **All staged datasets use young actors in controlled settings.** Older-adult fall kinematics (slower, sliding, assisted) are under-represented.
2. **None match our geometry:** 160° fisheye, ceiling-corner mount, IR at night.
3. **Licences are mostly research-only or unstated.** Training a commercial model on them is a legal risk until each is confirmed in writing. The registry blocks unverified sets from training by policy.
4. OmniFall's taxonomy (fall / lie_down / sit_down / stand_up + static fallen / lying / sitting / standing, walk, other) is the right label space: it separates *intentional lying* from *falls* and *fallen* from *lying*.

**Do not blindly combine datasets.** Each dataset is a separate evaluation slice; cross-dataset generalisation is reported per slice.

## 2. Split policy

- Split by **subject** (dataset-qualified), else by **video**. Never by frame.
- Deterministic hash assignment (`ai/preprocessing/splits.py`): adding data never moves existing clips between splits; `check_no_leakage()` runs before every training job.
- Thresholds are chosen on VAL. TEST is evaluated once per frozen model.
- Report per-dataset and per-condition (day/IR night, occluded/unoccluded).

## 3. VisionAID's own dataset (critical path)

Public data cannot validate the product. Collect on the production camera:

- **Protocol:** consenting adult actors incl. trained older-adult actors, physiotherapist-supervised, crash mats; falls forward/backward/sideways, from standing, from bed, from chair, sliding; recovery vs non-recovery.
- **ADLs (hard negatives):** getting into/out of bed, lying down, sitting, kneeling, bending, picking up objects, exercising, stretching, dropping objects, a caregiver bending over a resident.
- **Conditions:** day, dusk, IR night; occlusion by beds/curtains; 2 mounting heights; 2 room layouts.
- **Annotation schema:** per clip `{onset_s, impact_s, rest_s, recovered_s?}` + OmniFall class spans; per-subject ID; camera config (resolution, quality, mount).
- **Governance:** written consent covering commercial ML training; data stored encrypted, access-logged; retention plan; no real-resident footage without a separate ethics approval and facility agreement.
- **Pilot data:** only incidents a caregiver has reviewed, under the facility's consent framework, and only if contractually permitted.
