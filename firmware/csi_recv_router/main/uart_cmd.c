/**
 * Wi-Vi Sentinel — ESP32 UART Command Handler
 *
 * Listens on UART0 for host commands (CMD:...) interleaved with normal
 * CSI_DATA output. Responses are prefixed with RESP: so the host can
 * distinguish them from CSI lines.
 */

#include "uart_cmd.h"
#include "wifi_nvs.h"

#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_netif.h"

static const char *TAG = "uart_cmd";

#define UART_NUM       UART_NUM_0
#define UART_BUF_SIZE  512
#define CMD_PREFIX     "CMD:"
#define RESP_PREFIX    "RESP:"

/* ── Command handlers ────────────────────────────────────────────────────── */

static void handle_wifi_set(const char *payload)
{
    /* payload format: <ssid>|<password> */
    char ssid[33] = {0};
    char pass[65] = {0};

    const char *sep = strchr(payload, '|');
    if (!sep) {
        printf(RESP_PREFIX "ERR:Invalid format, expected SSID|PASSWORD\n");
        return;
    }

    size_t ssid_len = sep - payload;
    if (ssid_len == 0 || ssid_len > 32) {
        printf(RESP_PREFIX "ERR:SSID must be 1-32 characters\n");
        return;
    }

    memcpy(ssid, payload, ssid_len);
    ssid[ssid_len] = '\0';

    size_t pass_len = strlen(sep + 1);
    if (pass_len > 64) pass_len = 64;
    memcpy(pass, sep + 1, pass_len);
    pass[pass_len] = '\0';

    esp_err_t err = wifi_nvs_save(ssid, pass);
    if (err != ESP_OK) {
        printf(RESP_PREFIX "ERR:NVS write failed (%s)\n", esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "WiFi credentials saved: SSID=%s", ssid);
    printf(RESP_PREFIX "OK:WiFi credentials saved, rebooting in 2s\n");
    fflush(stdout);

    vTaskDelay(pdMS_TO_TICKS(2000));
    esp_restart();
}

static void handle_wifi_status(void)
{
    wifi_ap_record_t ap_info;
    esp_netif_ip_info_t ip_info;
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");

    if (!netif || esp_wifi_sta_get_ap_info(&ap_info) != ESP_OK) {
        printf(RESP_PREFIX "ERR:Not connected\n");
        return;
    }

    esp_netif_get_ip_info(netif, &ip_info);

    printf(RESP_PREFIX "OK:%s|" IPSTR "|%d|%d\n",
           ap_info.ssid,
           IP2STR(&ip_info.ip),
           ap_info.primary,
           ap_info.rssi);
}

/* ── UART listener task ──────────────────────────────────────────────────── */

static void uart_cmd_task(void *arg)
{
    uint8_t buf[UART_BUF_SIZE];
    char line[UART_BUF_SIZE];
    int line_pos = 0;

    while (1) {
        int len = uart_read_bytes(UART_NUM, buf, sizeof(buf) - 1, pdMS_TO_TICKS(100));
        if (len <= 0) continue;

        for (int i = 0; i < len; i++) {
            char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (line_pos > 0) {
                    line[line_pos] = '\0';

                    /* Only process lines starting with CMD: */
                    if (strncmp(line, CMD_PREFIX, strlen(CMD_PREFIX)) == 0) {
                        const char *cmd = line + strlen(CMD_PREFIX);

                        if (strncmp(cmd, "WIFI_SET:", 9) == 0) {
                            handle_wifi_set(cmd + 9);
                        } else if (strcmp(cmd, "WIFI_STATUS") == 0) {
                            handle_wifi_status();
                        } else {
                            printf(RESP_PREFIX "ERR:Unknown command: %s\n", cmd);
                        }
                    }
                    line_pos = 0;
                }
            } else if (line_pos < (int)sizeof(line) - 1) {
                line[line_pos++] = c;
            }
        }
    }
}

/* ── Public API ──────────────────────────────────────────────────────────── */

esp_err_t uart_cmd_start(void)
{
    /* UART0 is already initialized by ESP-IDF for console output.
     * We just install the driver so we can also read from it. */
    uart_config_t uart_config = {
        .baud_rate = CONFIG_ESP_CONSOLE_UART_BAUDRATE,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
    };

    esp_err_t err = uart_param_config(UART_NUM, &uart_config);
    if (err != ESP_OK) return err;

    err = uart_driver_install(UART_NUM, UART_BUF_SIZE * 2, 0, 0, NULL, 0);
    if (err != ESP_OK) return err;

    xTaskCreate(uart_cmd_task, "uart_cmd", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "UART command listener started");

    return ESP_OK;
}
