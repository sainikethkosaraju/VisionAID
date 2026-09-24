/*
 * Uplink: Wi-Fi with backoff, SNTP, HTTPS heartbeat/events/evidence.
 *
 * Alert path first: the fall event (≈300 bytes JSON) is sent before any evidence, so
 * alert latency never waits on image upload. Evidence is best-effort and bounded.
 */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

#include "cJSON.h"
#include "esp_camera.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "visionaid.h"

static const char *TAG = "uplink";

#define WIFI_UP BIT0
#define FRAMES_PER_UPLOAD 40
#define FRAME_COPY_MAX (160u * 1024u)
#define RESP_MAX 2048

static EventGroupHandle_t s_net;
static va_config_t *s_cfg;
static uint32_t s_backoff_ms = 1000;
static esp_timer_handle_t s_reconnect;

static void reconnect_cb(void *arg)
{
    (void)arg;
    esp_wifi_connect();
}

/* ------------------------------------------------------------------ Wi-Fi */

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_net, WIFI_UP);
        /* Exponential backoff with jitter, capped at 60 s; never give up. */
        uint32_t wait = s_backoff_ms + (esp_random() % 500);
        s_backoff_ms = s_backoff_ms < 60000 ? s_backoff_ms * 2 : 60000;
        ESP_LOGW(TAG, "Wi-Fi down; retry in %" PRIu32 " ms", wait);
        /* Never block the system event loop: schedule the reconnect. */
        esp_timer_stop(s_reconnect);
        esp_timer_start_once(s_reconnect, (uint64_t)wait * 1000);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_backoff_ms = 1000;
        xEventGroupSetBits(s_net, WIFI_UP);
    }
}

static void wifi_start(void)
{
    s_net = xEventGroupCreate();
    const esp_timer_create_args_t t = {.callback = reconnect_cb, .name = "wifi_retry"};
    ESP_ERROR_CHECK(esp_timer_create(&t, &s_reconnect));
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi, NULL));
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, s_cfg->wifi_ssid, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, s_cfg->wifi_pass, sizeof wc.sta.password);
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wc.sta.pmf_cfg.capable = true;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE)); /* latency over power: mains device */
    ESP_ERROR_CHECK(esp_wifi_start());

    esp_sntp_config_t sntp = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&sntp);
}

static bool net_up(TickType_t wait)
{
    return xEventGroupWaitBits(s_net, WIFI_UP, pdFALSE, pdTRUE, wait) & WIFI_UP;
}

/* ------------------------------------------------------------------ time */

static int64_t wall_ms_for(uint64_t mono_ms)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    int64_t now_wall = (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
    return now_wall - (int64_t)(va_mono_ms() - mono_ms);
}

static void iso8601(int64_t wall_ms, char *out, size_t cap)
{
    time_t s = (time_t)(wall_ms / 1000);
    struct tm tm;
    gmtime_r(&s, &tm);
    snprintf(out, cap, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", tm.tm_year + 1900,
             tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec,
             (int)(wall_ms % 1000));
}

/* ------------------------------------------------------------------ HTTP */

typedef struct {
    char *buf;
    int len;
} resp_t;

static esp_err_t on_http(esp_http_client_event_t *e)
{
    resp_t *r = e->user_data;
    if (e->event_id == HTTP_EVENT_ON_DATA && r && r->len + e->data_len < RESP_MAX) {
        memcpy(r->buf + r->len, e->data, e->data_len);
        r->len += e->data_len;
        r->buf[r->len] = 0;
    }
    return ESP_OK;
}

static esp_http_client_handle_t client_for(const char *path, resp_t *resp)
{
    char url[256];
    snprintf(url, sizeof url, "%s%s", s_cfg->api_base, path);
    esp_http_client_config_t c = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 10000,
        .crt_bundle_attach = esp_crt_bundle_attach, /* verify server certificate */
        .event_handler = on_http,
        .user_data = resp,
    };
    esp_http_client_handle_t h = esp_http_client_init(&c);
    char auth[176];
    snprintf(auth, sizeof auth, "Bearer %s", s_cfg->device_token);
    esp_http_client_set_header(h, "Authorization", auth);
    return h;
}

static int post_json(const char *path, const char *body, char *resp_buf)
{
    resp_t resp = {.buf = resp_buf, .len = 0};
    if (resp_buf) resp_buf[0] = 0;
    esp_http_client_handle_t h = client_for(path, resp_buf ? &resp : NULL);
    esp_http_client_set_header(h, "Content-Type", "application/json");
    esp_http_client_set_post_field(h, body, (int)strlen(body));
    int status = esp_http_client_perform(h) == ESP_OK ? esp_http_client_get_status_code(h)
                                                      : -1;
    esp_http_client_cleanup(h);
    return status;
}

/* ------------------------------------------------------------------ heartbeat */

static void apply_config(const char *json)
{
    cJSON *root = cJSON_Parse(json);
    if (!root) return;
    cJSON *cfg = cJSON_GetObjectItem(root, "config");
    cJSON *v;
    if (cfg && (v = cJSON_GetObjectItem(cfg, "buffer_seconds")) && cJSON_IsNumber(v)) {
        s_cfg->buffer_seconds = (uint32_t)v->valuedouble;
        xSemaphoreTake(g_buf.lock, portMAX_DELAY);
        vb_set_window(&g_buf.ring, s_cfg->buffer_seconds);
        xSemaphoreGive(g_buf.lock);
    }
    if (cfg && (v = cJSON_GetObjectItem(cfg, "pre_event_seconds")) && cJSON_IsNumber(v))
        s_cfg->pre_event_seconds = (uint32_t)v->valuedouble;
    if (cfg && (v = cJSON_GetObjectItem(cfg, "post_event_seconds")) && cJSON_IsNumber(v))
        s_cfg->post_event_seconds = (uint32_t)v->valuedouble;
    if (cfg && (v = cJSON_GetObjectItem(cfg, "heartbeat_seconds")) && cJSON_IsNumber(v))
        s_cfg->heartbeat_seconds = (uint32_t)v->valuedouble;
    cJSON *cmds = cJSON_GetObjectItem(root, "commands");
    cJSON *c;
    cJSON_ArrayForEach(c, cmds)
    {
        cJSON *name = cJSON_GetObjectItem(c, "command");
        if (cJSON_IsString(name) && strcmp(name->valuestring, "restart") == 0) {
            ESP_LOGW(TAG, "restart requested by server");
            cJSON_Delete(root);
            esp_restart();
        }
        /* self_test / capture_diagnostics: PENDING — acknowledged via next heartbeat. */
    }
    cJSON_Delete(root);
}

static void send_heartbeat(void)
{
    vb_stats_t st;
    xSemaphoreTake(g_buf.lock, portMAX_DELAY);
    vb_stats(&g_buf.ring, &st);
    xSemaphoreGive(g_buf.lock);

    wifi_ap_record_t ap;
    int rssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
    char ts[32];
    iso8601(wall_ms_for(va_mono_ms()), ts, sizeof ts);

    cJSON *b = cJSON_CreateObject();
    cJSON_AddStringToObject(b, "timestamp", ts);
    cJSON_AddStringToObject(b, "firmware_version", VISIONAID_FW_VERSION);
    cJSON_AddStringToObject(b, "model_version",
                            detector_available() ? "pedestrian-pico-s8+ft-v0" : "none");
    cJSON_AddNumberToObject(b, "uptime_seconds", (double)(va_mono_ms() / 1000));
    if (rssi) cJSON_AddNumberToObject(b, "rssi_dbm", rssi);
    cJSON_AddNumberToObject(b, "free_psram_bytes",
                            (double)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    cJSON_AddNumberToObject(b, "buffer_effective_seconds", st.effective_seconds);
    cJSON_AddStringToObject(b, "power_state", "mains");
    cJSON *faults = cJSON_AddArrayToObject(b, "faults");
    if (!detector_available()) cJSON_AddItemToArray(faults, cJSON_CreateString(
                                   "detector_unavailable"));
    if (st.frames == 0) cJSON_AddItemToArray(faults, cJSON_CreateString("no_frames"));
    char *body = cJSON_PrintUnformatted(b);
    cJSON_Delete(b);

    char *resp = malloc(RESP_MAX);
    int status = post_json("/device/heartbeat", body, resp);
    if (status == 200) {
        apply_config(resp);
    } else {
        ESP_LOGW(TAG, "heartbeat failed: %d", status);
    }
    free(resp);
    cJSON_free(body);
}

/* ------------------------------------------------------------------ evidence */

/* Upload frames [first, last] in batches as multipart/form-data. */
static void upload_frames(const char *incident_id, const char *segment, uint32_t first,
                          uint32_t last)
{
    static const char *B = "visionaidframeboundary7f3a";
    uint8_t *copy = heap_caps_malloc(FRAME_COPY_MAX, MALLOC_CAP_SPIRAM);
    if (!copy) return;
    char path[160];
    snprintf(path, sizeof path, "/device/incidents/%s/evidence?segment=%s", incident_id,
             segment);

    for (uint32_t start = first; start <= last; start += FRAMES_PER_UPLOAD) {
        uint32_t end = start + FRAMES_PER_UPLOAD - 1 < last ? start + FRAMES_PER_UPLOAD - 1
                                                            : last;
        /* Pass 1: content length (frames are pinned, so sizes are stable). */
        int total = 0, n = 0;
        char part[192];
        for (uint32_t s = start; s <= end; s++) {
            uint32_t len;
            uint64_t ts;
            xSemaphoreTake(g_buf.lock, portMAX_DELAY);
            bool ok = vb_get(&g_buf.ring, s, NULL, &len, &ts) == VB_OK;
            xSemaphoreGive(g_buf.lock);
            if (!ok || len > FRAME_COPY_MAX) continue;
            total += snprintf(part, sizeof part,
                              "--%s\r\nContent-Disposition: form-data; name=\"frames\"; "
                              "filename=\"%" PRId64 ".jpg\"\r\nContent-Type: image/jpeg"
                              "\r\n\r\n", B, wall_ms_for(ts)) + (int)len + 2;
            n++;
        }
        if (n == 0) continue;
        total += (int)strlen(B) + 6;

        esp_http_client_handle_t h = client_for(path, NULL);
        char ctype[80];
        snprintf(ctype, sizeof ctype, "multipart/form-data; boundary=%s", B);
        esp_http_client_set_header(h, "Content-Type", ctype);
        if (esp_http_client_open(h, total) != ESP_OK) {
            esp_http_client_cleanup(h);
            break;
        }
        /* Pass 2: stream. Each frame is copied out under the lock, sent without it. */
        for (uint32_t s = start; s <= end; s++) {
            const uint8_t *data;
            uint32_t len;
            uint64_t ts;
            xSemaphoreTake(g_buf.lock, portMAX_DELAY);
            bool ok = vb_get(&g_buf.ring, s, &data, &len, &ts) == VB_OK &&
                      len <= FRAME_COPY_MAX;
            if (ok) memcpy(copy, data, len);
            xSemaphoreGive(g_buf.lock);
            if (!ok) continue;
            int hl = snprintf(part, sizeof part,
                              "--%s\r\nContent-Disposition: form-data; name=\"frames\"; "
                              "filename=\"%" PRId64 ".jpg\"\r\nContent-Type: image/jpeg"
                              "\r\n\r\n", B, wall_ms_for(ts));
            esp_http_client_write(h, part, hl);
            esp_http_client_write(h, (const char *)copy, (int)len);
            esp_http_client_write(h, "\r\n", 2);
            esp_task_wdt_reset();
        }
        int tl = snprintf(part, sizeof part, "--%s--\r\n", B);
        esp_http_client_write(h, part, tl);
        esp_http_client_fetch_headers(h);
        int status = esp_http_client_get_status_code(h);
        esp_http_client_cleanup(h);
        if (status == 503) {
            ESP_LOGI(TAG, "server evidence storage disabled; discarding frames by policy");
            break;
        }
        if (status != 201) ESP_LOGW(TAG, "evidence batch failed: %d", status);
    }
    free(copy);
}

/* ------------------------------------------------------------------ events */

static char s_incident[40];
static bool s_capturing;
static uint64_t s_post_until_ms;
static uint32_t s_post_first_seq;

static void handle_trigger(const va_msg_t *m)
{
    uint32_t first_seq;
    uint64_t pre_from = m->event_mono_ms > (uint64_t)s_cfg->pre_event_seconds * 1000
                            ? m->event_mono_ms - (uint64_t)s_cfg->pre_event_seconds * 1000
                            : 0;
    xSemaphoreTake(g_buf.lock, portMAX_DELAY);
    vb_pin(&g_buf.ring, pre_from, &first_seq);
    uint32_t last_seq = g_buf.ring.next_seq - 1;
    xSemaphoreGive(g_buf.lock);

    char ts[32], evid[40], body[384];
    iso8601(wall_ms_for(m->event_mono_ms), ts, sizeof ts);
    uint8_t mac[6];
    esp_efuse_mac_get_default(mac);
    snprintf(evid, sizeof evid, "%02x%02x%02x-%" PRIu64, mac[3], mac[4], mac[5],
             m->event_mono_ms);
    snprintf(body, sizeof body,
             "{\"event_id\":\"%s\",\"occurred_at\":\"%s\",\"trigger_score\":%.3f,"
             "\"descent_detected\":%s,\"immobility_seconds\":%.1f,\"recovered\":false}",
             evid, ts, m->trigger_score, m->descent ? "true" : "false", m->immobility_s);

    char *resp = malloc(RESP_MAX);
    int status = -1;
    /* Alert path: retry hard. The event_id makes retries idempotent server-side. */
    /* Each blocking step is < the 15 s task watchdog and is preceded by a reset. */
    for (int attempt = 0; attempt < 8 && status != 200; attempt++) {
        esp_task_wdt_reset();
        if (!net_up(pdMS_TO_TICKS(5000))) continue;
        esp_task_wdt_reset();
        status = post_json("/device/events/fall", body, resp);
        esp_task_wdt_reset();
        if (status != 200) vTaskDelay(pdMS_TO_TICKS(500u << (attempt < 4 ? attempt : 4)));
    }
    s_incident[0] = 0;
    cJSON *root = status == 200 ? cJSON_Parse(resp) : NULL;
    cJSON *id = root ? cJSON_GetObjectItem(root, "incident_id") : NULL;
    cJSON *accepted = root ? cJSON_GetObjectItem(root, "evidence_accepted") : NULL;
    if (cJSON_IsString(id)) strlcpy(s_incident, id->valuestring, sizeof s_incident);
    bool upload = s_incident[0] && cJSON_IsTrue(accepted);
    cJSON_Delete(root);
    free(resp);

    if (!s_incident[0]) {
        ESP_LOGE(TAG, "fall event not delivered after retries (status %d)", status);
        xSemaphoreTake(g_buf.lock, portMAX_DELAY);
        vb_unpin(&g_buf.ring);
        xSemaphoreGive(g_buf.lock);
        return;
    }
    if (upload) {
        upload_frames(s_incident, "pre", first_seq, last_seq);
        s_capturing = true;
        s_post_first_seq = last_seq + 1;
        s_post_until_ms = m->event_mono_ms + (uint64_t)s_cfg->post_event_seconds * 1000;
    } else {
        xSemaphoreTake(g_buf.lock, portMAX_DELAY);
        vb_unpin(&g_buf.ring);
        xSemaphoreGive(g_buf.lock);
    }
}

static void finish_post_window(void)
{
    if (!s_capturing || va_mono_ms() < s_post_until_ms) return;
    xSemaphoreTake(g_buf.lock, portMAX_DELAY);
    uint32_t last = g_buf.ring.next_seq - 1;
    xSemaphoreGive(g_buf.lock);
    if (last >= s_post_first_seq) upload_frames(s_incident, "post", s_post_first_seq, last);
    xSemaphoreTake(g_buf.lock, portMAX_DELAY);
    vb_unpin(&g_buf.ring);
    xSemaphoreGive(g_buf.lock);
    s_capturing = false;
}

static void handle_observation(const va_msg_t *m)
{
    if (!s_incident[0]) return;
    char path[96], body[160];
    snprintf(path, sizeof path, "/device/incidents/%s/observations", s_incident);
    snprintf(body, sizeof body, "{\"immobility_seconds\":%.1f,\"recovered\":%s}",
             m->immobility_s, m->kind == VA_MSG_RECOVERED ? "true" : "false");
    int status = post_json(path, body, NULL);
    if (status != 200) ESP_LOGW(TAG, "observation failed: %d", status);
}

void uplink_task(void *arg)
{
    s_cfg = (va_config_t *)arg;
    wifi_start();
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    uint64_t next_hb = 0;
    for (;;) {
        esp_task_wdt_reset();
        va_msg_t m;
        if (xQueueReceive(g_uplink_q, &m, pdMS_TO_TICKS(250)) == pdTRUE) {
            if (m.kind == VA_MSG_TRIGGER) handle_trigger(&m);
            else handle_observation(&m);
        }
        finish_post_window();
        if (va_mono_ms() >= next_hb && net_up(0)) {
            send_heartbeat();
            next_hb = va_mono_ms() + (uint64_t)s_cfg->heartbeat_seconds * 1000;
        }
    }
}
