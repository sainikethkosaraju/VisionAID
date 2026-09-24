/* Host-side unit tests for portable edge components. Build: make -C edge/esp32/tests/host */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fall_trigger.h"
#include "vision_buffer.h"

static int failures, checks;
#define CHECK(cond)                                                              \
    do {                                                                         \
        checks++;                                                                \
        if (!(cond)) {                                                           \
            failures++;                                                          \
            fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);    \
        }                                                                        \
    } while (0)

/* ------------------------------------------------------------------ vision_buffer */

static void fill(uint8_t *buf, uint32_t len, uint32_t seq)
{
    for (uint32_t i = 0; i < len; i++) buf[i] = (uint8_t)(seq * 31u + i);
}

static int intact(const vb_ring_t *r, uint32_t seq, uint32_t expect_len)
{
    const uint8_t *d;
    uint32_t len;
    if (vb_get(r, seq, &d, &len, NULL) != VB_OK || len != expect_len) return 0;
    for (uint32_t i = 0; i < len; i++)
        if (d[i] != (uint8_t)(seq * 31u + i)) return 0;
    return 1;
}

static void test_window_eviction(void)
{
    static uint8_t arena[1 << 20];
    static vb_desc_t slots[1024];
    vb_ring_t r;
    uint8_t frame[1000];
    CHECK(vb_init(&r, arena, sizeof arena, slots, 1024, 60) == VB_OK);
    /* 5 fps for 120 s: only the last ~60 s may remain. */
    for (uint32_t i = 0; i < 600; i++) {
        fill(frame, sizeof frame, i);
        CHECK(vb_push(&r, frame, sizeof frame, (uint64_t)i * 200, NULL) == VB_OK);
    }
    vb_stats_t st;
    vb_stats(&r, &st);
    CHECK(st.effective_seconds <= 60.0 && st.effective_seconds >= 59.0);
    CHECK(st.frames == 301);
    CHECK(st.evicted_age == 299);
    CHECK(intact(&r, 599, sizeof frame));
    CHECK(!vb_has(&r, 298));
}

static void test_byte_budget_and_wrap(void)
{
    static uint8_t arena[10000];
    static vb_desc_t slots[64];
    vb_ring_t r;
    uint8_t frame[3000];
    vb_init(&r, arena, sizeof arena, slots, 64, 3600);
    for (uint32_t i = 0; i < 50; i++) {
        uint32_t len = 1000 + (i * 677) % 2000;
        fill(frame, len, i);
        uint32_t seq;
        CHECK(vb_push(&r, frame, len, i, &seq) == VB_OK && seq == i);
        CHECK(r.bytes_used <= sizeof arena);
        CHECK(intact(&r, i, len));
    }
    vb_stats_t st;
    vb_stats(&r, &st);
    CHECK(st.evicted_bytes > 0 && st.frames >= 3);
    CHECK(vb_push(&r, frame, sizeof arena + 1, 99, NULL) == VB_ERR_TOO_LARGE);
    CHECK(vb_push(&r, frame, 10, 1, NULL) == VB_ERR_ORDER);
}

/* Randomised comparison against a trivially-correct reference: the held frames must
 * always be a contiguous, intact suffix of what was pushed. */
static void test_randomised_model(void)
{
    static uint8_t arena[64 * 1024];
    static vb_desc_t slots[97];
    static uint32_t lens[20000];
    static uint8_t frame[9000];
    vb_ring_t r;
    vb_init(&r, arena, sizeof arena, slots, 97, 20);
    srand(12345);
    uint64_t ts = 0;
    for (uint32_t i = 0; i < 20000; i++) {
        lens[i] = 200 + (uint32_t)(rand() % 8800);
        ts += 50 + (uint64_t)(rand() % 300);
        fill(frame, lens[i], i);
        CHECK(vb_push(&r, frame, lens[i], ts, NULL) == VB_OK);
        if (i % 97 == 0) {
            uint32_t held = r.count;
            CHECK(r.oldest_seq + held == i + 1);
            for (uint32_t k = 0; k < held; k++) {
                if (!intact(&r, r.oldest_seq + k, lens[r.oldest_seq + k])) {
                    CHECK(0 && "frame corrupted");
                    return;
                }
            }
        }
    }
}

static void test_pin_protects_incident_window(void)
{
    static uint8_t arena[20000];
    static vb_desc_t slots[128];
    vb_ring_t r;
    uint8_t frame[1000];
    vb_init(&r, arena, sizeof arena, slots, 128, 60);
    for (uint32_t i = 0; i < 30; i++) {
        fill(frame, sizeof frame, i);
        vb_push(&r, frame, sizeof frame, (uint64_t)i * 200, NULL);
    }
    /* Fall at t=5000 ms; preserve from t=3000 (seq 15). */
    uint32_t first;
    vb_pin(&r, 3000, &first);
    CHECK(first == 15);
    int dropped = 0;
    for (uint32_t i = 30; i < 60; i++) {
        fill(frame, sizeof frame, i);
        if (vb_push(&r, frame, sizeof frame, (uint64_t)i * 200, NULL) == VB_ERR_PINNED_FULL)
            dropped++;
    }
    CHECK(dropped > 0);                 /* arena exhausted while pinned: new frames drop */
    for (uint32_t s = 15; s < 30; s++)  /* ...but every pinned frame survives intact */
        CHECK(intact(&r, s, sizeof frame));
    vb_stats_t st;
    vb_stats(&r, &st);
    CHECK(st.dropped_pinned == (uint32_t)dropped);
    vb_unpin(&r);
    fill(frame, sizeof frame, 1000);
    CHECK(vb_push(&r, frame, sizeof frame, 60 * 200, NULL) == VB_OK);
}

static void test_seek_and_clear(void)
{
    static uint8_t arena[50000];
    static vb_desc_t slots[64];
    vb_ring_t r;
    uint8_t frame[100] = {1};
    vb_init(&r, arena, sizeof arena, slots, 64, 60);
    for (uint32_t i = 0; i < 10; i++) vb_push(&r, frame, sizeof frame, 1000 + i * 100, NULL);
    uint32_t seq;
    CHECK(vb_seek(&r, 1250, &seq) == VB_OK && seq == 3);
    CHECK(vb_seek(&r, 0, &seq) == VB_OK && seq == 0);
    CHECK(vb_seek(&r, 99999, &seq) == VB_ERR_NOT_FOUND);
    vb_clear(&r);
    CHECK(r.count == 0);
    int nonzero = 0;
    for (size_t i = 0; i < sizeof arena; i++) nonzero |= arena[i];
    CHECK(nonzero == 0); /* cleared means zeroed, not just unlinked */
    CHECK(vb_get(&r, 5, NULL, NULL, NULL) == VB_ERR_NOT_FOUND);
}

/* ------------------------------------------------------------------ fall_trigger */

typedef struct { uint32_t t_ms; float cx, cy, w, h; int present; } key_t;

/* Feed a piecewise-linear trajectory sampled at `hz`; return counts of each event. */
typedef struct { int trigger, update, recovered; float immobile_max; ft_state_t s; } run_t;

static run_t simulate(const key_t *keys, int nkeys, float hz)
{
    run_t out;
    memset(&out, 0, sizeof out);
    ft_params_t p;
    ft_default_params(&p);
    ft_init(&out.s, &p);
    uint32_t step = (uint32_t)(1000.0f / hz);
    for (uint32_t t = 0; t <= keys[nkeys - 1].t_ms; t += step) {
        int k = 0;
        while (k < nkeys - 2 && keys[k + 1].t_ms <= t) k++;
        const key_t *a = &keys[k], *b = &keys[k + 1];
        float u = b->t_ms > a->t_ms ? (float)(t - a->t_ms) / (float)(b->t_ms - a->t_ms) : 0;
        if (u > 1) u = 1;
        ft_obs_t o = {.present = a->present && b->present,
                      .cx = a->cx + (b->cx - a->cx) * u, .cy = a->cy + (b->cy - a->cy) * u,
                      .w = a->w + (b->w - a->w) * u, .h = a->h + (b->h - a->h) * u,
                      .score = 0.8f, .ts_ms = t};
        switch (ft_update(&out.s, &o)) {
        case FT_EVT_TRIGGER: out.trigger++; break;
        case FT_EVT_UPDATE: out.update++; break;
        case FT_EVT_RECOVERED: out.recovered++; break;
        default: break;
        }
        if (out.s.immobile_s > out.immobile_max) out.immobile_max = out.s.immobile_s;
    }
    return out;
}

#define STAND(t) {t, 0.5f, 0.45f, 0.2f, 0.5f, 1}
#define FALLEN(t) {t, 0.55f, 0.75f, 0.45f, 0.2f, 1}

static void test_fast_fall_triggers_then_immobility(void)
{
    key_t k[] = {STAND(0), STAND(4000), FALLEN(4800), FALLEN(20000)};
    run_t r = simulate(k, 4, 5.0f);
    CHECK(r.trigger == 1);
    CHECK(r.immobile_max >= 10.0f);
    CHECK(r.update >= 2);
    CHECK(r.recovered == 0);
    CHECK(r.s.trigger_score >= 0.55f);
}

static void test_standing_and_walking_do_not_trigger(void)
{
    key_t stand[] = {STAND(0), STAND(20000)};
    CHECK(simulate(stand, 2, 5.0f).trigger == 0);
    key_t walk[] = {{0, 0.1f, 0.45f, 0.2f, 0.5f, 1}, {8000, 0.9f, 0.45f, 0.2f, 0.5f, 1},
                    {16000, 0.1f, 0.47f, 0.2f, 0.48f, 1}};
    CHECK(simulate(walk, 3, 5.0f).trigger == 0);
}

static void test_intentional_lying_down_does_not_trigger(void)
{
    /* Lying down onto a bed over 4 s: large shape change, slow descent. */
    key_t k[] = {STAND(0), STAND(3000), {7000, 0.55f, 0.72f, 0.45f, 0.2f, 1},
                 {20000, 0.55f, 0.72f, 0.45f, 0.2f, 1}};
    CHECK(simulate(k, 4, 5.0f).trigger == 0);
}

static void test_sitting_and_bending_do_not_trigger(void)
{
    key_t sit[] = {STAND(0), STAND(3000), {3500, 0.5f, 0.52f, 0.25f, 0.35f, 1},
                   {10000, 0.5f, 0.52f, 0.25f, 0.35f, 1}};
    CHECK(simulate(sit, 4, 5.0f).trigger == 0); /* fast "plop" into a chair */
    key_t bend[] = {STAND(0), STAND(2000), {3200, 0.5f, 0.5f, 0.3f, 0.38f, 1},
                    {5000, 0.5f, 0.5f, 0.3f, 0.38f, 1}, STAND(6500), STAND(9000)};
    CHECK(simulate(bend, 6, 5.0f).trigger == 0);
}

static void test_fall_then_recovery(void)
{
    key_t k[] = {STAND(0), STAND(4000), FALLEN(4800), FALLEN(8000), STAND(10000),
                 STAND(15000)};
    run_t r = simulate(k, 6, 5.0f);
    CHECK(r.trigger == 1);
    CHECK(r.recovered == 1);
    CHECK(r.s.phase == FT_MONITORING);
}

static void test_occlusion_after_fall_is_not_recovery_or_immobility(void)
{
    key_t k[] = {STAND(0), STAND(4000), FALLEN(4800), FALLEN(5000),
                 {5200, 0, 0, 0, 0, 0}, {30000, 0, 0, 0, 0, 0}};
    run_t r = simulate(k, 6, 5.0f);
    CHECK(r.trigger == 1);
    CHECK(r.recovered == 0);
    CHECK(r.immobile_max < 1.0f);
}

static void test_low_frame_rate_still_triggers(void)
{
    /* 3 Hz inference: fall spans only 2-3 samples. */
    key_t k[] = {STAND(0), STAND(4000), FALLEN(4700), FALLEN(15000)};
    CHECK(simulate(k, 4, 3.0f).trigger == 1);
}

int main(void)
{
    struct { const char *name; void (*fn)(void); } tests[] = {
        {"vb window eviction", test_window_eviction},
        {"vb byte budget + wrap", test_byte_budget_and_wrap},
        {"vb randomised model", test_randomised_model},
        {"vb pin protects incident window", test_pin_protects_incident_window},
        {"vb seek + clear", test_seek_and_clear},
        {"ft fast fall", test_fast_fall_triggers_then_immobility},
        {"ft standing/walking", test_standing_and_walking_do_not_trigger},
        {"ft intentional lying", test_intentional_lying_down_does_not_trigger},
        {"ft sitting/bending", test_sitting_and_bending_do_not_trigger},
        {"ft recovery", test_fall_then_recovery},
        {"ft occlusion", test_occlusion_after_fall_is_not_recovery_or_immobility},
        {"ft 3 Hz", test_low_frame_rate_still_triggers},
    };
    for (size_t i = 0; i < sizeof tests / sizeof tests[0]; i++) {
        int before = failures;
        tests[i].fn();
        printf("%s %s\n", failures == before ? "ok  " : "FAIL", tests[i].name);
    }
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
