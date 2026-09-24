/*
 * VisionAID ephemeral vision buffer.
 *
 * A byte-budgeted ring of variable-size JPEG frames held in RAM (PSRAM on the ESP32-S3).
 * New frames overwrite the oldest. Nothing is ever written to flash by this module.
 *
 * Eviction happens for three reasons, oldest-first:
 *   1. age      — frame older than window_ms (the configured buffer duration)
 *   2. bytes    — arena has no contiguous room for the new frame
 *   3. slots    — descriptor table full
 *
 * Incident preservation: vb_pin(ts) freezes every frame captured at or after `ts`
 * (pre-event window) and every frame written afterwards (post-event window) until
 * vb_unpin(). A pinned frame is never evicted; if the arena fills while pinned, the
 * NEW frame is dropped and counted (evidence integrity beats recency during an upload).
 *
 * Single-owner and not thread-safe: the firmware wraps calls in a mutex.
 * Portable C99, no ESP-IDF dependencies, so it is unit-tested on the host.
 */
#ifndef VISION_BUFFER_H
#define VISION_BUFFER_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VB_OK = 0,
    VB_ERR_ARG = -1,
    VB_ERR_TOO_LARGE = -2,   /* frame larger than the whole arena */
    VB_ERR_PINNED_FULL = -3, /* no room without evicting pinned frames: frame dropped */
    VB_ERR_NOT_FOUND = -4,   /* sequence number evicted or never written */
    VB_ERR_ORDER = -5,       /* timestamp went backwards */
} vb_err_t;

typedef struct {
    uint32_t offset;
    uint32_t len;
    uint64_t ts_ms;   /* monotonic capture time */
} vb_desc_t;

typedef struct {
    uint8_t *arena;
    uint32_t arena_size;
    vb_desc_t *slots;
    uint32_t slot_count;
    uint64_t window_ms;

    uint32_t head;       /* next write offset in arena */
    uint32_t tail_slot;  /* slot index of oldest frame */
    uint32_t count;      /* frames held */
    uint32_t oldest_seq; /* sequence number of oldest frame */
    uint32_t next_seq;   /* sequence number the next frame will receive */
    uint32_t bytes_used;

    bool pinned;
    uint32_t pin_seq;    /* frames with seq >= pin_seq are protected */

    uint32_t evicted_age, evicted_bytes, evicted_slots, dropped_pinned;
} vb_ring_t;

typedef struct {
    uint32_t frames;
    uint32_t bytes_used;
    uint32_t arena_size;
    double effective_seconds; /* newest.ts - oldest.ts */
    uint32_t evicted_age, evicted_bytes, evicted_slots, dropped_pinned;
    bool pinned;
} vb_stats_t;

/* arena and slots are caller-owned (e.g. heap_caps_malloc(..., MALLOC_CAP_SPIRAM)). */
vb_err_t vb_init(vb_ring_t *r, uint8_t *arena, uint32_t arena_size, vb_desc_t *slots,
                 uint32_t slot_count, uint32_t window_seconds);

/* Change the retention window at runtime (config pushed from the backend). */
void vb_set_window(vb_ring_t *r, uint32_t window_seconds);

/* Copy a frame in. On success *seq_out (optional) receives its sequence number. */
vb_err_t vb_push(vb_ring_t *r, const uint8_t *data, uint32_t len, uint64_t ts_ms,
                 uint32_t *seq_out);

/* Borrow a frame. Pointer valid until the frame is evicted — pin before long reads. */
vb_err_t vb_get(const vb_ring_t *r, uint32_t seq, const uint8_t **data, uint32_t *len,
                uint64_t *ts_ms);

/* First held sequence number with ts >= ts_ms; VB_ERR_NOT_FOUND if none. */
vb_err_t vb_seek(const vb_ring_t *r, uint64_t ts_ms, uint32_t *seq_out);

/* Protect frames from ts_ms onward (and all future frames) until vb_unpin. */
vb_err_t vb_pin(vb_ring_t *r, uint64_t from_ts_ms, uint32_t *first_seq_out);
void vb_unpin(vb_ring_t *r);

/* Zero and forget everything (e.g. privacy mode, shutdown). */
void vb_clear(vb_ring_t *r);

void vb_stats(const vb_ring_t *r, vb_stats_t *out);

static inline bool vb_has(const vb_ring_t *r, uint32_t seq)
{
    return r->count > 0 && (uint32_t)(seq - r->oldest_seq) < r->count;
}

#ifdef __cplusplus
}
#endif
#endif /* VISION_BUFFER_H */
