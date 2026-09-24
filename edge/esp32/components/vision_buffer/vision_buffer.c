#include "vision_buffer.h"

#include <string.h>

static inline vb_desc_t *slot_of(const vb_ring_t *r, uint32_t seq)
{
    uint32_t idx = (r->tail_slot + (seq - r->oldest_seq)) % r->slot_count;
    return &r->slots[idx];
}

static inline bool is_pinned(const vb_ring_t *r, uint32_t seq)
{
    return r->pinned && (int32_t)(seq - r->pin_seq) >= 0;
}

/* Evict the oldest frame. Returns false if it is pinned. */
static bool evict_oldest(vb_ring_t *r)
{
    if (r->count == 0 || is_pinned(r, r->oldest_seq)) {
        return false;
    }
    vb_desc_t *d = &r->slots[r->tail_slot];
    /* Forget, don't just unlink: overwrite pixels so no stale imagery lingers. */
    memset(r->arena + d->offset, 0, d->len);
    r->bytes_used -= d->len;
    r->tail_slot = (r->tail_slot + 1) % r->slot_count;
    r->oldest_seq++;
    r->count--;
    if (r->count == 0) {
        r->head = 0;
    }
    return true;
}

vb_err_t vb_init(vb_ring_t *r, uint8_t *arena, uint32_t arena_size, vb_desc_t *slots,
                 uint32_t slot_count, uint32_t window_seconds)
{
    if (!r || !arena || !slots || arena_size == 0 || slot_count == 0 || window_seconds == 0) {
        return VB_ERR_ARG;
    }
    memset(r, 0, sizeof(*r));
    r->arena = arena;
    r->arena_size = arena_size;
    r->slots = slots;
    r->slot_count = slot_count;
    r->window_ms = (uint64_t)window_seconds * 1000u;
    memset(arena, 0, arena_size);
    return VB_OK;
}

void vb_set_window(vb_ring_t *r, uint32_t window_seconds)
{
    if (window_seconds > 0) {
        r->window_ms = (uint64_t)window_seconds * 1000u;
    }
}

vb_err_t vb_push(vb_ring_t *r, const uint8_t *data, uint32_t len, uint64_t ts_ms,
                 uint32_t *seq_out)
{
    if (!r || !data || len == 0) {
        return VB_ERR_ARG;
    }
    if (len > r->arena_size) {
        return VB_ERR_TOO_LARGE;
    }
    if (r->count > 0 && ts_ms < slot_of(r, r->next_seq - 1)->ts_ms) {
        return VB_ERR_ORDER;
    }

    /* 1. Age: forget anything outside the window. Pinned frames survive. */
    while (r->count > 0 && ts_ms - r->slots[r->tail_slot].ts_ms > r->window_ms) {
        if (!evict_oldest(r)) {
            break;
        }
        r->evicted_age++;
    }

    /* 2. Slots. */
    while (r->count >= r->slot_count) {
        if (!evict_oldest(r)) {
            r->dropped_pinned++;
            return VB_ERR_PINNED_FULL;
        }
        r->evicted_slots++;
    }

    /* 3. Contiguous bytes. Live data occupies [oldest.offset, head) circularly. */
    for (;;) {
        if (r->count == 0) {
            r->head = 0;
            break;
        }
        uint32_t oldest_off = r->slots[r->tail_slot].offset;
        if (oldest_off >= r->head) {
            /* Free gap is [head, oldest_off). */
            if (oldest_off - r->head >= len) {
                break;
            }
        } else {
            /* Free space is [head, end) and [0, oldest_off). Use the end if it fits. */
            if (r->arena_size - r->head >= len) {
                break;
            }
            r->head = 0; /* wrap; the tail gap is wasted until the next lap */
            continue;
        }
        if (!evict_oldest(r)) {
            r->dropped_pinned++;
            return VB_ERR_PINNED_FULL;
        }
        r->evicted_bytes++;
    }

    uint32_t seq = r->next_seq;
    if (r->count == 0) {
        r->oldest_seq = seq;
    }
    vb_desc_t *d = &r->slots[(r->tail_slot + r->count) % r->slot_count];
    d->offset = r->head;
    d->len = len;
    d->ts_ms = ts_ms;
    memcpy(r->arena + r->head, data, len);
    r->head += len;
    r->bytes_used += len;
    r->count++;
    r->next_seq++;
    if (seq_out) {
        *seq_out = seq;
    }
    return VB_OK;
}

vb_err_t vb_get(const vb_ring_t *r, uint32_t seq, const uint8_t **data, uint32_t *len,
                uint64_t *ts_ms)
{
    if (!r || !vb_has(r, seq)) {
        return VB_ERR_NOT_FOUND;
    }
    const vb_desc_t *d = slot_of(r, seq);
    if (data) *data = r->arena + d->offset;
    if (len) *len = d->len;
    if (ts_ms) *ts_ms = d->ts_ms;
    return VB_OK;
}

vb_err_t vb_seek(const vb_ring_t *r, uint64_t ts_ms, uint32_t *seq_out)
{
    if (!r || r->count == 0) {
        return VB_ERR_NOT_FOUND;
    }
    /* Timestamps are monotonic: binary search over sequence numbers. */
    uint32_t lo = 0, hi = r->count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (slot_of(r, r->oldest_seq + mid)->ts_ms < ts_ms) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo == r->count) {
        return VB_ERR_NOT_FOUND;
    }
    if (seq_out) *seq_out = r->oldest_seq + lo;
    return VB_OK;
}

vb_err_t vb_pin(vb_ring_t *r, uint64_t from_ts_ms, uint32_t *first_seq_out)
{
    uint32_t seq;
    if (vb_seek(r, from_ts_ms, &seq) != VB_OK) {
        seq = r->next_seq; /* nothing held that recent: protect from the next frame */
    }
    r->pinned = true;
    r->pin_seq = seq;
    if (first_seq_out) *first_seq_out = seq;
    return VB_OK;
}

void vb_unpin(vb_ring_t *r)
{
    r->pinned = false;
}

void vb_clear(vb_ring_t *r)
{
    memset(r->arena, 0, r->arena_size);
    r->head = r->tail_slot = r->count = r->bytes_used = 0;
    r->oldest_seq = r->next_seq;
    r->pinned = false;
}

void vb_stats(const vb_ring_t *r, vb_stats_t *out)
{
    memset(out, 0, sizeof(*out));
    out->frames = r->count;
    out->bytes_used = r->bytes_used;
    out->arena_size = r->arena_size;
    if (r->count > 1) {
        uint64_t newest = slot_of(r, r->next_seq - 1)->ts_ms;
        uint64_t oldest = r->slots[r->tail_slot].ts_ms;
        out->effective_seconds = (double)(newest - oldest) / 1000.0;
    }
    out->evicted_age = r->evicted_age;
    out->evicted_bytes = r->evicted_bytes;
    out->evicted_slots = r->evicted_slots;
    out->dropped_pinned = r->dropped_pinned;
    out->pinned = r->pinned;
}
