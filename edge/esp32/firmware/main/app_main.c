/*
 * VisionAID camera firmware — task layout
 *
 *   capture_task  (core 1, prio 6)  camera → ephemeral ring buffer at CAPTURE_FPS
 *   detect_task   (core 1, prio 5)  newest frame → person detector → fall trigger
 *   uplink_task   (core 0, prio 4)  Wi-Fi, heartbeat, events, evidence upload
 *
 * Nothing is written to flash during normal operation. Frames live only in PSRAM and
 * are overwritten continuously (docs/BUFFER_ARCHITECTURE.md).
 */
#include <string.h>

#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "visionaid.h"

static const char *TAG = "visionaid";

#define CAPTURE_FPS 4 /* HVGA@4fps fits the arena at worst-case frame size */
#define ARENA_BYTES (4608u * 1024u) /* 4.5 MB of 8 MB PSRAM; see buffer budget */
#define MAX_SLOTS (CAPTURE_FPS * 120 + 64) /* supports buffer_seconds up to 120 */
#define DETECT_SCRATCH_BYTES (96u * 1024u)

va_buffer_t g_buf;
QueueHandle_t g_uplink_q;
va_config_t g_cfg;

uint64_t va_mono_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

static void capture_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    TickType_t last = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(1000 / CAPTURE_FPS);
    for (;;) {
        esp_task_wdt_reset();
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) {
            if (fb->format == PIXFORMAT_JPEG) {
                xSemaphoreTake(g_buf.lock, portMAX_DELAY);
                vb_err_t err = vb_push(&g_buf.ring, fb->buf, (uint32_t)fb->len, va_mono_ms(),
                                       NULL);
                xSemaphoreGive(g_buf.lock);
                if (err != VB_OK && err != VB_ERR_PINNED_FULL) {
                    ESP_LOGW(TAG, "vb_push: %d (len=%u)", err, (unsigned)fb->len);
                }
            }
            esp_camera_fb_return(fb);
        } else {
            ESP_LOGW(TAG, "camera frame timeout");
        }
        vTaskDelayUntil(&last, period);
    }
}

static void detect_task(void *arg)
{
    (void)arg;
    uint8_t *scratch = heap_caps_malloc(DETECT_SCRATCH_BYTES, MALLOC_CAP_SPIRAM);
    ft_params_t params;
    ft_default_params(&params);
    ft_state_t ft;
    ft_init(&ft, &params);
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));

    for (;;) {
        esp_task_wdt_reset();
        if (!detector_available() || scratch == NULL) {
            /* Reported as a fault in every heartbeat; see detector.c. */
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        /* Copy the newest frame out under the lock; inference runs without it. */
        uint32_t len = 0;
        uint64_t ts = 0;
        xSemaphoreTake(g_buf.lock, portMAX_DELAY);
        const uint8_t *data;
        bool ok = g_buf.ring.count > 0 &&
                  vb_get(&g_buf.ring, g_buf.ring.next_seq - 1, &data, &len, &ts) == VB_OK &&
                  len <= DETECT_SCRATCH_BYTES;
        if (ok) memcpy(scratch, data, len);
        xSemaphoreGive(g_buf.lock);
        if (!ok) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        ft_obs_t obs = {.present = false, .ts_ms = ts};
        if (detector_run(scratch, len, &obs) != ESP_OK) continue;
        obs.ts_ms = ts;

        ft_event_t ev = ft_update(&ft, &obs);
        if (ev == FT_EVT_NONE) continue;
        va_msg_t m = {
            .kind = ev == FT_EVT_TRIGGER     ? VA_MSG_TRIGGER
                    : ev == FT_EVT_RECOVERED ? VA_MSG_RECOVERED
                                             : VA_MSG_UPDATE,
            .event_mono_ms = ev == FT_EVT_TRIGGER ? ft.fall_ts_ms : ts,
            .trigger_score = ft.trigger_score,
            .descent = ft.descent,
            .immobility_s = ft.immobile_s,
            .recovered = ft.recovered,
        };
        if (xQueueSend(g_uplink_q, &m, 0) != pdTRUE) {
            ESP_LOGE(TAG, "uplink queue full; event %d dropped", m.kind);
        }
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    if (provisioning_load(&g_cfg) != ESP_OK) {
        ESP_LOGE(TAG, "not provisioned; halting (run tools/provision.py)");
        for (;;) vTaskDelay(portMAX_DELAY);
    }

    uint8_t *arena = heap_caps_malloc(ARENA_BYTES, MALLOC_CAP_SPIRAM);
    vb_desc_t *slots = heap_caps_calloc(MAX_SLOTS, sizeof(vb_desc_t), MALLOC_CAP_SPIRAM);
    if (!arena || !slots) {
        ESP_LOGE(TAG, "PSRAM allocation failed (free=%u)",
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        esp_restart();
    }
    ESP_ERROR_CHECK(vb_init(&g_buf.ring, arena, ARENA_BYTES, slots, MAX_SLOTS,
                            g_cfg.buffer_seconds) == VB_OK ? ESP_OK : ESP_FAIL);
    g_buf.lock = xSemaphoreCreateMutex();
    g_uplink_q = xQueueCreate(16, sizeof(va_msg_t));

    if (camera_start() != ESP_OK) {
        /* Keep the uplink alive so the fault is visible on the dashboard. */
        ESP_LOGE(TAG, "camera unavailable");
    } else {
        xTaskCreatePinnedToCore(capture_task, "capture", 4096, NULL, 6, NULL, 1);
    }
    if (detector_init() != ESP_OK) {
        ESP_LOGW(TAG, "detector unavailable (PENDING INTEGRATION)");
    }
    xTaskCreatePinnedToCore(detect_task, "detect", 8192, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(uplink_task, "uplink", 8192, &g_cfg, 4, NULL, 0);
    ESP_LOGI(TAG, "VisionAID %s started; PSRAM free %u", VISIONAID_FW_VERSION,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}
