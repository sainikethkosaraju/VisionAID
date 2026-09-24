idf_component_register(
    SRCS "app_main.c" "camera.c" "uplink.c" "detector.c" "provisioning.c"
    INCLUDE_DIRS "."
    REQUIRES vision_buffer fall_trigger esp_wifi esp_netif esp_event nvs_flash
             esp_http_client esp_timer esp-tls json esp_system esp_driver_gpio
    PRIV_REQUIRES esp32-camera)
