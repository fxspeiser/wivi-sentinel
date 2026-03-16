/**
 * Wi-Vi Sentinel — ESP32 UART Command Handler
 *
 * Listens on UART0 for commands from the host (Pi / Mac) and responds.
 * Protocol:
 *   Request:  CMD:<command>:<payload>\n
 *   Response: RESP:<OK|ERR>:<message>\n
 *
 * Supported commands:
 *   CMD:WIFI_SET:<ssid>|<password>   — Store WiFi creds in NVS, reboot
 *   CMD:WIFI_STATUS                  — Return current WiFi connection info
 */

#pragma once

#include "esp_err.h"

/**
 * Start the UART command listener task.
 * Call this from app_main() after WiFi is initialized.
 */
esp_err_t uart_cmd_start(void);
