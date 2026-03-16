/**
 * Wi-Vi Sentinel — NVS WiFi Credential Loader
 */

#include "wifi_nvs.h"
#include "uart_cmd.h"   /* for nvs_load_wifi() */

#include <string.h>
#include "esp_log.h"

/* Kconfig defaults compiled into the firmware */
#ifndef CONFIG_EXAMPLE_WIFI_SSID
#define CONFIG_EXAMPLE_WIFI_SSID ""
#endif
#ifndef CONFIG_EXAMPLE_WIFI_PASSWORD
#define CONFIG_EXAMPLE_WIFI_PASSWORD ""
#endif

static const char *TAG = "wifi_nvs";

bool wifi_nvs_load(char *ssid, char *pass)
{
    /* Try NVS first */
    char nvs_ssid[33] = {0};
    char nvs_pass[65] = {0};

    if (nvs_load_wifi(nvs_ssid, sizeof(nvs_ssid), nvs_pass, sizeof(nvs_pass)) == ESP_OK
        && strlen(nvs_ssid) > 0) {
        strncpy(ssid, nvs_ssid, 32);
        strncpy(pass, nvs_pass, 64);
        ESP_LOGI(TAG, "Using NVS WiFi credentials: SSID=%s", ssid);
        return true;
    }

    /* Fall back to Kconfig defaults */
    strncpy(ssid, CONFIG_EXAMPLE_WIFI_SSID, 32);
    strncpy(pass, CONFIG_EXAMPLE_WIFI_PASSWORD, 64);
    ESP_LOGI(TAG, "Using Kconfig WiFi credentials: SSID=%s", ssid);
    return false;
}
