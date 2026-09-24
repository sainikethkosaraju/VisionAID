/*
 * OV3660 on DFRobot DFR1154. Pin map verified against DFRobot's official example
 * (wiki.dfrobot.com/dfr1154/docs/21371). Resolution/quality are the *initial* choice
 * pending on-device frame-size measurement (docs/BUFFER_ARCHITECTURE.md §Measurement).
 */
#include "esp_camera.h"
#include "esp_log.h"
#include "visionaid.h"

static const char *TAG = "camera";

#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 5
#define CAM_PIN_SIOD 8
#define CAM_PIN_SIOC 9
#define CAM_PIN_D7 4 /* Y9 */
#define CAM_PIN_D6 6 /* Y8 */
#define CAM_PIN_D5 7 /* Y7 */
#define CAM_PIN_D4 14 /* Y6 */
#define CAM_PIN_D3 17 /* Y5 */
#define CAM_PIN_D2 21 /* Y4 */
#define CAM_PIN_D1 18 /* Y3 */
#define CAM_PIN_D0 16 /* Y2 */
#define CAM_PIN_VSYNC 1
#define CAM_PIN_HREF 2
#define CAM_PIN_PCLK 15

esp_err_t camera_start(void)
{
    camera_config_t c = {
        .pin_pwdn = CAM_PIN_PWDN,
        .pin_reset = CAM_PIN_RESET,
        .pin_xclk = CAM_PIN_XCLK,
        .pin_sccb_sda = CAM_PIN_SIOD,
        .pin_sccb_scl = CAM_PIN_SIOC,
        .pin_d7 = CAM_PIN_D7,
        .pin_d6 = CAM_PIN_D6,
        .pin_d5 = CAM_PIN_D5,
        .pin_d4 = CAM_PIN_D4,
        .pin_d3 = CAM_PIN_D3,
        .pin_d2 = CAM_PIN_D2,
        .pin_d1 = CAM_PIN_D1,
        .pin_d0 = CAM_PIN_D0,
        .pin_vsync = CAM_PIN_VSYNC,
        .pin_href = CAM_PIN_HREF,
        .pin_pclk = CAM_PIN_PCLK,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG, /* sensor-side JPEG: no CPU encode cost */
        .frame_size = FRAMESIZE_HVGA,   /* 480x320 — see buffer budget */
        .jpeg_quality = 14,             /* lower = better quality, larger frames */
        .fb_count = 2,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_LATEST, /* never queue stale frames */
    };
    esp_err_t err = esp_camera_init(&c);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "camera init failed: %s", esp_err_to_name(err));
        return err;
    }
    sensor_t *s = esp_camera_sensor_get();
    if (s && s->id.PID != OV3660_PID) {
        ESP_LOGW(TAG, "unexpected sensor PID 0x%x (expected OV3660)", s->id.PID);
    }
    return ESP_OK;
}
