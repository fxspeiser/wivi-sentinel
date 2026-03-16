/**
 * Wi-Vi Sentinel — NVS WiFi Credential Storage
 */

#include "wifi_nvs.h"

#include <string.h>
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"

/* Kconfig defaults compiled into the firmware */
#ifndef CONFIG_EXAMPLE_WIFI_SSID
#define CONFIG_EXAMPLE_WIFI_SSID ""
#endif
#ifndef CONFIG_EXAMPLE_WIFI_PASSWORD
#define CONFIG_EXAMPLE_WIFI_PASSWORD ""
#endif

static const char *TAG = "wifi_nvs";

#define NVS_NAMESPACE  "wivi_wifi"
#define NVS_KEY_SSID   "ssid"
#define NVS_KEY_PASS   "password"

esp_err_t wifi_nvs_save(const char *ssid, const char *password)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;

    err = nvs_set_str(handle, NVS_KEY_SSID, ssid);
    if (err == ESP_OK)
        err = nvs_set_str(handle, NVS_KEY_PASS, password);
    if (err == ESP_OK)
        err = nvs_commit(handle);

    nvs_close(handle);
    return err;
}

bool wifi_nvs_load(char *ssid, char *pass)
{
    nvs_handle_t handle;
    char nvs_ssid[33] = {0};
    char nvs_pass[65] = {0};
    size_t ssid_len = sizeof(nvs_ssid);
    size_t pass_len = sizeof(nvs_pass);

    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err == ESP_OK) {
        err = nvs_get_str(handle, NVS_KEY_SSID, nvs_ssid, &ssid_len);
        if (err == ESP_OK) {
            pass_len = sizeof(nvs_pass);
            nvs_get_str(handle, NVS_KEY_PASS, nvs_pass, &pass_len);
        }
        nvs_close(handle);
    }

    if (err == ESP_OK && strlen(nvs_ssid) > 0) {
        memcpy(ssid, nvs_ssid, 33);
        memcpy(pass, nvs_pass, 65);
        ESP_LOGI(TAG, "Using NVS WiFi credentials: SSID=%s", ssid);
        return true;
    }

    /* Fall back to Kconfig defaults */
    memcpy(ssid, CONFIG_EXAMPLE_WIFI_SSID, strlen(CONFIG_EXAMPLE_WIFI_SSID) + 1);
    memcpy(pass, CONFIG_EXAMPLE_WIFI_PASSWORD, strlen(CONFIG_EXAMPLE_WIFI_PASSWORD) + 1);
    ESP_LOGI(TAG, "Using Kconfig WiFi credentials: SSID=%s", ssid);
    return false;
}
