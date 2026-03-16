/**
 * Wi-Vi Sentinel — NVS WiFi Credential Loader
 *
 * On boot, checks NVS for stored WiFi credentials (set by the dashboard).
 * If found, these override the Kconfig defaults compiled into the firmware.
 * If not found, falls back to CONFIG_EXAMPLE_WIFI_SSID / _PASSWORD.
 */

#pragma once

#include <stdbool.h>

/**
 * Load WiFi credentials, preferring NVS over Kconfig defaults.
 *
 * @param ssid    Buffer to receive SSID (min 33 bytes)
 * @param pass    Buffer to receive password (min 65 bytes)
 * @return true if NVS credentials were loaded, false if using Kconfig defaults
 */
bool wifi_nvs_load(char *ssid, char *pass);
