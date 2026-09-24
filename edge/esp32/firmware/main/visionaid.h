/* Shared firmware declarations. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "fall_trigger.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "vision_buffer.h"

#define VISIONAID_FW_VERSION "0.1.0-dev"

/* ---- Runtime configuration (NVS-provisioned; server may update windows) ---------- */
typedef struct {
    char wifi_ssid[33];
    char wifi_pass[65];
    char api_base[128];     /* e.g. https://api.example.org/api/v1 — TLS required */
    char device_token[160]; /* "<device_uuid>.<secret>", provisioned once, never logged */
    uint32_t buffer_seconds;
    uint32_t pre_event_seconds;
    uint32_t post_event_seconds;
    uint32_t heartbeat_seconds;
} va_config_t;

esp_err_t provisioning_load(va_config_t *cfg);

/* ---- Camera --------------------------------------------------------------------- */
esp_err_t camera_start(void);

/* ---- Buffer shared by capture (writer) and uplink (reader) ----------------------- */
typedef struct {
    vb_ring_t ring;
    SemaphoreHandle_t lock;
} va_buffer_t;

extern va_buffer_t g_buf;

/* ---- Detector (PENDING INTEGRATION) ---------------------------------------------- */
/* Runs person detection on a JPEG frame. Returns ESP_ERR_NOT_SUPPORTED until the ESP-DL
 * pedestrian model is integrated; callers must report the "detector_unavailable" fault. */
esp_err_t detector_init(void);
esp_err_t detector_run(const uint8_t *jpeg, uint32_t len, ft_obs_t *out);
bool detector_available(void);

/* ---- Uplink ---------------------------------------------------------------------- */
typedef enum { VA_MSG_TRIGGER, VA_MSG_UPDATE, VA_MSG_RECOVERED } va_msg_kind_t;

typedef struct {
    va_msg_kind_t kind;
    uint64_t event_mono_ms;
    float trigger_score;
    bool descent;
    float immobility_s;
    bool recovered;
} va_msg_t;

extern QueueHandle_t g_uplink_q;

void uplink_task(void *arg);
uint64_t va_mono_ms(void);
