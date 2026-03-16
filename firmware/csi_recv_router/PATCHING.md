# Patching csi_recv_router for Wi-Vi Sentinel

These files add UART command support and NVS WiFi credential storage to
Espressif's `csi_recv_router` example firmware. This lets you change WiFi
credentials from the Wi-Vi Sentinel dashboard without reflashing.

## Files

- `main/uart_cmd.c` / `.h` — UART command listener (WIFI_SET, WIFI_STATUS)
- `main/wifi_nvs.c` / `.h` — NVS credential loader (overrides Kconfig defaults)

## How to patch

After cloning esp-csi and before building:

```bash
# 1. Copy these files into the firmware project
ESP_CSI=~/esp/esp-csi/examples/get-started/csi_recv_router
cp main/uart_cmd.c main/uart_cmd.h "$ESP_CSI/main/"
cp main/wifi_nvs.c main/wifi_nvs.h "$ESP_CSI/main/"

# 2. Add the new source files to CMakeLists.txt
#    In $ESP_CSI/main/CMakeLists.txt, add uart_cmd.c and wifi_nvs.c
#    to the SRCS list in idf_component_register().

# 3. Patch app_main() to:
#    a) Call wifi_nvs_load() to get SSID/password before wifi_init_sta()
#    b) Call uart_cmd_start() after WiFi is initialized

# 4. Build and flash
cd "$ESP_CSI"
idf.py build && idf.py flash -p /dev/cu.usbserial-110
```

## Protocol

The host sends commands over the same serial connection used for CSI data:

```
→  CMD:WIFI_SET:MyNetwork|MyPassword\n
←  RESP:OK:WiFi credentials saved, rebooting in 2s\n

→  CMD:WIFI_STATUS\n
←  RESP:OK:MyNetwork|192.168.1.219|11|-45\n
```

The ESP32 reboots after WIFI_SET to connect with the new credentials.
CSI_DATA lines continue to flow normally between commands.

## setup_esp32.sh

The setup script (`setup_esp32.sh` in the repo root) automates this patching.
