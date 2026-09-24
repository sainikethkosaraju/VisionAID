/*
 * Device configuration from NVS namespace "visionaid".
 *
 * Secrets (Wi-Fi password, device token) are written once at installation by the
 * provisioning tool (edge/esp32/tools/provision.py) and are never compiled into firmware.
 * Production builds should enable NVS encryption + flash encryption (docs/SECURITY.md).
 */
#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "visionaid.h"

static const char *TAG = "provision";

static esp_err_t get_str(nvs_handle_t h, const char *key, char *out, size_t cap)
{
    size_t len = cap;
    esp_err_t err = nvs_get_str(h, key, out, &len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "missing NVS key '%s'", key);
    }
    return err;
}

esp_err_t provisioning_load(va_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->buffer_seconds = 60;
    cfg->pre_event_seconds = 30;
    cfg->post_event_seconds = 15;
    cfg->heartbeat_seconds = 30;

    nvs_handle_t h;
    esp_err_t err = nvs_open("visionaid", NVS_READONLY, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "device not provisioned (nvs_open: %s)", esp_err_to_name(err));
        return err;
    }
    err = get_str(h, "wifi_ssid", cfg->wifi_ssid, sizeof cfg->wifi_ssid);
    if (err == ESP_OK) err = get_str(h, "wifi_pass", cfg->wifi_pass, sizeof cfg->wifi_pass);
    if (err == ESP_OK) err = get_str(h, "api_base", cfg->api_base, sizeof cfg->api_base);
    if (err == ESP_OK)
        err = get_str(h, "device_token", cfg->device_token, sizeof cfg->device_token);
    uint32_t v;
    if (nvs_get_u32(h, "buffer_s", &v) == ESP_OK && v > 0) cfg->buffer_seconds = v;
    nvs_close(h);

    if (err == ESP_OK && strncmp(cfg->api_base, "https://", 8) != 0) {
        ESP_LOGE(TAG, "api_base must use https://");
        return ESP_ERR_INVALID_ARG;
    }
    return err;
}
