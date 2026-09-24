# Ephemeral Vision Buffer

> **Observe temporarily. Forget continuously.** Implemented: `edge/esp32/components/vision_buffer` (portable C, host-tested under ASan/UBSan) and wired into firmware (compiles; not yet run on hardware).

## 1. Requirements

| Setting | Default | Where |
|---|---|---|
| `VISIONAID_BUFFER_SECONDS` | 60 (30/60/120 supported) | backend → pushed to device on every heartbeat |
| `VISIONAID_PRE_EVENT_SECONDS` | 30 | backend; must be ≤ buffer seconds (validated) |
| `VISIONAID_POST_EVENT_SECONDS` | 15 | backend |

No module hard-codes 60 s; the device starts with its NVS value and adopts the server's value at the first heartbeat.

## 2. Memory budget

PSRAM: 8 MB total.

| Consumer | Estimate |
|---|---|
| Ring arena | **4.5 MB** (`ARENA_BYTES`) |
| Descriptor table (≤ 544 slots × 16 B) | < 10 KB |
| Camera frame buffers (2 × JPEG, PSRAM) | ~0.1–0.3 MB |
| Detector scratch + decoded RGB input + model activations (PENDING measurement) | ~0.5–1 MB |
| Wi-Fi/LwIP buffers (PSRAM-allowed), TLS, upload copy buffer (160 KB) | ~0.3–0.5 MB |
| Headroom | ≥ 1.5 MB |

Buffer requirement: `fps × seconds × mean_frame_bytes × (1 + wrap_waste)`.
`edge/esp32/tools/buffer_budget.py` output for a 60 s window (frame sizes are **planning estimates** for OV3660 sensor-side JPEG, not measurements):

```
res      KB/frame      2fps       |    3fps       |    4fps       |    5fps       |    8fps
QVGA         5-10 0.6-1.2MB ok | 0.9-1.8MB ok | 1.2-2.5MB ok | 1.5-3.1MB ok | 2.5-4.9MB ~
HVGA         9-18 1.1-2.2MB ok | 1.7-3.3MB ok | 2.2-4.4MB ok | 2.8-5.5MB ~  | 4.4-8.9MB ~
VGA         15-30 1.8-3.7MB ok | 2.8-5.5MB ~  | 3.7-7.4MB ~  | 4.6-9.2MB X  | 7.4-14.8MB X
SVGA        25-45 3.1-5.5MB ~  | 4.6-8.3MB X  | 6.2-11.1MB X | 7.7-13.8MB X | 12.3-22.1MB X
ok = fits worst case · ~ = fits only at low end · X = does not fit
```

## 3. Decision: HVGA (480×320) JPEG @ 4 fps, PSRAM only

- **HVGA over QVGA:** the server verifier needs pixels on a person across a room seen through a 160° lens. QVGA leaves a distant person at tens of pixels tall.
- **Not VGA+:** doesn't fit 60 s at any useful rate.
- **4 fps:** fits the worst-case estimate; detection runs on the newest buffered frame, and the trigger is tested to work at 3 Hz. Falls take roughly 0.5–1.5 s, so 2–6 samples span the descent.
- **Sensor-side JPEG:** OV3660 encodes in hardware; no CPU cost. Detection decodes at reduced scale.
- **Lower resolution is also a privacy feature:** evidence is sufficient for incident review, not identity surveillance.

Revisit after measurement (§6). If mean HVGA frames exceed ~18 KB, options in order: raise `jpeg_quality` number (smaller files) → 3 fps → QVGA.

## 4. Why not flash, SD card or thousands of files

- The rolling buffer writes ~60 KB/s continuously (4 fps × ~15 KB) ≈ 5.2 GB/day ≈ 325 full rewrites of the 16 MB flash per day. At a typical ~100k erase-cycle rating that is under a year *even with perfect wear levelling* — for a device expected to last 5+ years — and filesystem churn adds latency. **The rolling buffer never touches flash.**
- SD card: same wear argument, plus removable media is a privacy liability. Reserved for a future *store-and-forward spool of incident windows only* when the network is down (rare writes).
- One file per frame (create/delete thousands per minute) is exactly the anti-pattern the spec forbids.

## 5. Design

A **byte-budgeted ring of variable-size JPEG records** in one contiguous PSRAM arena, plus a circular descriptor table `{offset, len, ts_ms}` indexed by a monotonically increasing sequence number.

```
arena:  [ F17 | F18 | F19 | ....free.... | F12 | F13 | F14 | F15 | F16 |waste]
                        ^head (next write)  ^oldest (tail)
```

Eviction (oldest first) happens for **age** (older than the window), **bytes** (no contiguous room) or **slots** (table full). Evicted frames are **zeroed**, not merely unlinked, so stale imagery cannot linger in memory. `vb_clear()` zeroes the whole arena.

Wrap: variable-size records can't straddle the arena end, so when the tail gap is too small the writer wraps to offset 0 and the gap is wasted for that lap (bounded by one frame; budgeted at 5%).

### Incident preservation — pinning

```
t_fall − 30 s                t_fall              t_fall + 15 s
     │◄──── pre-event (pinned) ───►│◄── post-event (pinned) ──►│  unpin → forgotten
```

`vb_pin(t_fall − pre)` protects every held frame from that timestamp and every frame written afterwards. Pinned frames are never evicted. If the arena fills while pinned, the **new** frame is dropped and counted (`dropped_pinned`) — evidence integrity beats recency during an upload. With a 60 s buffer and a 45 s incident window there is 15 s of slack plus upload time; the counter tells us in the field whether that is enough.

The alert path does not wait on any of this: the fall event is sent first.

### Self-reporting
Every heartbeat reports `buffer_effective_seconds` (newest − oldest held frame). If it drops below `PRE_EVENT_SECONDS`, the backend marks the camera **DEGRADED** — the system never silently holds less context than it promises.

### Concurrency
The ring is single-owner C; firmware serialises access with one FreeRTOS mutex. Readers copy a frame out under the lock and do slow work (inference, TLS) without it.

## 6. Measurement plan (first hardware task)

1. Flash firmware with a diagnostic build logging `fb->len` per frame.
2. For each of QVGA/HVGA/VGA × `jpeg_quality` {10, 12, 14, 16}: record 5 min each of (a) empty room day, (b) person moving day, (c) IR night empty, (d) IR night with motion.
3. Record mean, p95 and max frame size; `vb_stats()` effective seconds; free PSRAM after detector load.
4. Feed means into `buffer_budget.py --measured HVGA=<kb>` and finalise the default.

## 7. Tests (host, `make -C edge/esp32/tests/host`)
Window eviction; byte-budget + wrap; 20,000-frame randomised comparison against a reference model (held frames always an intact contiguous suffix); pinning protects the incident window and drops new frames rather than evidence; seek; clear zeroes memory.
