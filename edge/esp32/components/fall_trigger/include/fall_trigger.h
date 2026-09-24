/*
 * VisionAID on-device fall trigger — heuristic v0 (bounding-box temporal reasoning).
 *
 * Input: one person detection per inference tick (bbox from ESP-DL pedestrian detector).
 * Output: a temporal state machine that fires a TRIGGER on a *rapid* downward collapse
 * with body-shape change, then tracks post-fall immobility and recovery.
 *
 * It deliberately does NOT treat "person is lying down" as a fall: slow transitions
 * (lying on a bed, sitting, kneeling) produce low descent velocity and do not trigger.
 *
 * The trigger is tuned for RECALL. Precision comes from the backend confidence engine
 * and (when deployed) the server-side pose/temporal verifier. All thresholds are
 * parameters; defaults are starting points to be tuned on recorded data, not validated
 * values (see docs/AI_MODEL.md).
 *
 * Wide-angle (160°) lens caveat: bbox geometry is distorted near image edges. Thresholds
 * are normalised by the person's own upright bbox height to reduce, not remove, this.
 */
#ifndef FALL_TRIGGER_H
#define FALL_TRIGGER_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FT_HISTORY 32

typedef enum {
    FT_MONITORING = 0, /* NORMAL: no descent in progress */
    FT_GROUND,         /* descent detected; tracking immobility / recovery */
} ft_phase_t;

typedef enum {
    FT_EVT_NONE = 0,
    FT_EVT_TRIGGER,  /* send fall event to backend now */
    FT_EVT_UPDATE,   /* periodic post-fall observation */
    FT_EVT_RECOVERED,/* person upright again */
} ft_event_t;

typedef struct {
    bool present;
    float cx, cy, w, h;   /* normalised [0,1] image coordinates; y grows downward */
    float score;          /* detector confidence */
    uint64_t ts_ms;
} ft_obs_t;

typedef struct {
    float min_detect_score;       /* ignore detections below this */
    float upright_aspect;         /* h/w above this = upright */
    float lying_aspect;           /* h/w below this = horizontal */
    uint32_t descent_window_ms;   /* look-back for velocity / shape change */
    float velocity_lo, velocity_hi; /* descent speed, upright-heights per second */
    float height_drop_lo, height_drop_hi; /* 1 - h/h_upright */
    float w_velocity, w_height, w_aspect; /* score weights (sum to 1) */
    float trigger_threshold;      /* score to fire */
    float still_motion;           /* centroid motion (upright-heights/s) counted as still */
    uint32_t recover_hold_ms;     /* upright this long after a fall = recovered */
    uint32_t update_interval_ms;  /* FT_EVT_UPDATE cadence while on ground */
    uint32_t ground_timeout_ms;   /* stop tracking and return to monitoring */
} ft_params_t;

typedef struct {
    ft_params_t p;
    ft_phase_t phase;
    ft_obs_t hist[FT_HISTORY];
    uint32_t n, head;
    float upright_h;              /* EMA of upright bbox height (0 = unknown) */

    /* Post-fall tracking */
    uint64_t fall_ts_ms, last_ts_ms, last_update_ms, upright_since_ms;
    float immobile_s;
    float last_cx, last_cy;
    bool have_last;
    bool recovered;
    float trigger_score;
    bool descent;
} ft_state_t;

void ft_default_params(ft_params_t *p);
void ft_init(ft_state_t *s, const ft_params_t *p);
ft_event_t ft_update(ft_state_t *s, const ft_obs_t *o);

/* Score in [0,1] for the most recent window; exposed for diagnostics and tests. */
float ft_descent_score(const ft_state_t *s, bool *descent_out);

#ifdef __cplusplus
}
#endif
#endif
