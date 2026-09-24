/*
 * Person detector — PENDING INTEGRATION.
 *
 * Target: ESP-DL `pedestrian_detect` (pico_s8_v1_s3, 224x224x3). Espressif publishes
 * ~9 ms preprocess + ~118 ms inference + ~2 ms postprocess on ESP32-S3, i.e. ≈7 Hz max
 * on one core before JPEG decode. The integration decodes the buffered JPEG at 1/2 scale
 * (esp_jpeg), runs the model, and maps the best box into ft_obs_t.
 *
 * Until then this module reports itself unavailable. The firmware then raises the
 * "detector_unavailable" fault in every heartbeat, so the backend marks the camera
 * DEGRADED and the dashboard does not show the area as protected. It never fabricates
 * detections.
 */
#include "visionaid.h"

esp_err_t detector_init(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

bool detector_available(void)
{
    return false;
}

esp_err_t detector_run(const uint8_t *jpeg, uint32_t len, ft_obs_t *out)
{
    (void)jpeg;
    (void)len;
    (void)out;
    return ESP_ERR_NOT_SUPPORTED;
}
