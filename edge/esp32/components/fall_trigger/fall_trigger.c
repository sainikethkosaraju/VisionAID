#include "fall_trigger.h"

#include <math.h>
#include <string.h>

void ft_default_params(ft_params_t *p)
{
    p->min_detect_score = 0.35f;
    p->upright_aspect = 1.3f;
    p->lying_aspect = 0.9f;
    p->descent_window_ms = 1500;
    p->velocity_lo = 0.6f;   /* < 0.6 upright-heights/s: ordinary sitting/lying */
    p->velocity_hi = 1.6f;   /* >= 1.6: consistent with an uncontrolled fall */
    p->height_drop_lo = 0.25f;
    p->height_drop_hi = 0.55f;
    p->w_velocity = 0.5f;
    p->w_height = 0.3f;
    p->w_aspect = 0.2f;
    p->trigger_threshold = 0.55f;
    p->still_motion = 0.08f;
    p->recover_hold_ms = 2000;
    p->update_interval_ms = 5000;
    p->ground_timeout_ms = 120000;
}

void ft_init(ft_state_t *s, const ft_params_t *p)
{
    memset(s, 0, sizeof(*s));
    s->p = *p;
}

static float clamp01(float x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }

static float ramp(float x, float lo, float hi)
{
    return hi > lo ? clamp01((x - lo) / (hi - lo)) : (x >= hi ? 1.0f : 0.0f);
}

static const ft_obs_t *hist_at(const ft_state_t *s, uint32_t back)
{
    /* back = 0 → newest */
    uint32_t idx = (s->head + FT_HISTORY - 1 - back) % FT_HISTORY;
    return &s->hist[idx];
}

float ft_descent_score(const ft_state_t *s, bool *descent_out)
{
    if (descent_out) *descent_out = false;
    if (s->n < 2 || s->upright_h <= 0) {
        return 0;
    }
    const ft_obs_t *now = hist_at(s, 0);
    if (!now->present) {
        return 0;
    }
    /* Find the most upright-looking sample within the window as the "before" state. */
    float best_v = 0, best_aspect_before = 0;
    for (uint32_t b = 1; b < s->n; b++) {
        const ft_obs_t *prev = hist_at(s, b);
        uint64_t dt_ms = now->ts_ms - prev->ts_ms;
        if (dt_ms > s->p.descent_window_ms) break;
        if (!prev->present || dt_ms == 0) continue;
        /* Use the bbox top edge (head) — it moves most in a fall and is less affected
         * than the centre by legs being occluded by furniture. */
        float top_prev = prev->cy - prev->h / 2, top_now = now->cy - now->h / 2;
        float v = (top_now - top_prev) / s->upright_h / ((float)dt_ms / 1000.0f);
        if (v > best_v) {
            best_v = v;
            best_aspect_before = prev->w > 0 ? prev->h / prev->w : 0;
        }
    }
    float drop = 1.0f - now->h / s->upright_h;
    float aspect_now = now->w > 0 ? now->h / now->w : 0;
    float aspect_flip = (best_aspect_before >= s->p.upright_aspect &&
                         aspect_now <= s->p.lying_aspect) ? 1.0f : 0.0f;

    float score = s->p.w_velocity * ramp(best_v, s->p.velocity_lo, s->p.velocity_hi)
                + s->p.w_height * ramp(drop, s->p.height_drop_lo, s->p.height_drop_hi)
                + s->p.w_aspect * aspect_flip;
    /* A fall must include real downward speed; shape change alone is lying down. */
    bool descent = best_v >= s->p.velocity_lo && drop >= s->p.height_drop_lo;
    if (descent_out) *descent_out = descent;
    return clamp01(score);
}

static bool is_upright(const ft_state_t *s, const ft_obs_t *o)
{
    return o->present && o->w > 0 && o->h / o->w >= s->p.upright_aspect &&
           (s->upright_h <= 0 || o->h >= 0.8f * s->upright_h);
}

ft_event_t ft_update(ft_state_t *s, const ft_obs_t *in)
{
    ft_obs_t o = *in;
    if (o.present && o.score < s->p.min_detect_score) {
        o.present = false;
    }
    s->hist[s->head] = o;
    s->head = (s->head + 1) % FT_HISTORY;
    if (s->n < FT_HISTORY) s->n++;

    if (s->phase == FT_MONITORING) {
        if (is_upright(s, &o)) {
            s->upright_h = s->upright_h <= 0 ? o.h : 0.9f * s->upright_h + 0.1f * o.h;
        }
        bool descent = false;
        float score = ft_descent_score(s, &descent);
        if (descent && score >= s->p.trigger_threshold) {
            s->phase = FT_GROUND;
            s->fall_ts_ms = s->last_ts_ms = s->last_update_ms = o.ts_ms;
            s->upright_since_ms = 0;
            s->immobile_s = 0;
            s->recovered = false;
            s->trigger_score = score;
            s->descent = true;
            s->have_last = o.present;
            s->last_cx = o.cx;
            s->last_cy = o.cy;
            return FT_EVT_TRIGGER;
        }
        return FT_EVT_NONE;
    }

    /* FT_GROUND: accumulate immobility, watch for recovery. */
    float dt = (float)(o.ts_ms - s->last_ts_ms) / 1000.0f;
    s->last_ts_ms = o.ts_ms;
    if (o.present && s->have_last && s->upright_h > 0) {
        float dx = o.cx - s->last_cx, dy = o.cy - s->last_cy;
        float motion = sqrtf(dx * dx + dy * dy) / s->upright_h / (dt > 0 ? dt : 1e-3f);
        if (motion <= s->p.still_motion) {
            s->immobile_s += dt;
        } else {
            s->immobile_s = 0; /* immobility must be continuous */
        }
    }
    /* Person not visible after a fall (occluded by bed, out of frame): immobility is
     * unknown, so it is not accumulated — and it is never treated as recovery. */
    if (o.present) {
        s->have_last = true;
        s->last_cx = o.cx;
        s->last_cy = o.cy;
    }

    if (is_upright(s, &o)) {
        if (s->upright_since_ms == 0) s->upright_since_ms = o.ts_ms;
        if (o.ts_ms - s->upright_since_ms >= s->p.recover_hold_ms) {
            s->recovered = true;
            s->phase = FT_MONITORING;
            return FT_EVT_RECOVERED;
        }
    } else {
        s->upright_since_ms = 0;
    }

    if (o.ts_ms - s->fall_ts_ms >= s->p.ground_timeout_ms) {
        s->phase = FT_MONITORING; /* backend owns the incident from here */
        return FT_EVT_UPDATE;
    }
    if (o.ts_ms - s->last_update_ms >= s->p.update_interval_ms) {
        s->last_update_ms = o.ts_ms;
        return FT_EVT_UPDATE;
    }
    return FT_EVT_NONE;
}
