#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Wi-Vi Sentinel — ESP32 CSI Setup Script
# ═══════════════════════════════════════════════════════════════════════════════
#
# Automates the full ESP32 CSI setup:
#   1. Installs ESP-IDF (Espressif IoT Development Framework)
#   2. Clones the esp-csi repository
#   3. Configures WiFi credentials for the csi_recv_router firmware
#   4. Builds and flashes the firmware to the ESP32
#
# Requirements:
#   - macOS or Linux host machine
#   - ESP32-DevKitC-32E connected via USB
#   - Python 3.8+, git, cmake
#   - Your WiFi router must broadcast on 2.4 GHz (ESP32 is 2.4 GHz only)
#
# Usage:
#   chmod +x setup_esp32.sh
#   ./setup_esp32.sh
#
# Environment variables (optional — script will prompt if not set):
#   WIFI_SSID        Your 2.4 GHz WiFi network name
#   WIFI_PASSWORD    Your WiFi password
#   SERIAL_PORT      ESP32 serial port (auto-detected if not set)
#   ESP_IDF_DIR      ESP-IDF install location (default: ~/esp/esp-idf)
#   ESP_CSI_DIR      esp-csi install location (default: ~/esp/esp-csi)
# ═══════════════════════════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[ESP32]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
ask()  { echo -ne "${CYAN}[?]${NC} $1"; }

# ─── Defaults ────────────────────────────────────────────────────────────────

ESP_IDF_DIR="${ESP_IDF_DIR:-$HOME/esp/esp-idf}"
ESP_CSI_DIR="${ESP_CSI_DIR:-$HOME/esp/esp-csi}"
ESP_IDF_BRANCH="release/v5.4"  # stable release with ESP32 CSI support
FIRMWARE_DIR="examples/get-started/csi_recv_router"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── Detect OS ───────────────────────────────────────────────────────────────

OS="$(uname -s)"
case "$OS" in
    Darwin) log "Detected macOS" ;;
    Linux)  log "Detected Linux" ;;
    *)      err "Unsupported OS: $OS. This script supports macOS and Linux." ;;
esac

# ─── Check prerequisites ────────────────────────────────────────────────────

log "Checking prerequisites..."

command -v git >/dev/null 2>&1 || err "git is required. Install with: brew install git (macOS) or apt install git (Linux)"
command -v python3 >/dev/null 2>&1 || err "python3 is required. Install with: brew install python (macOS) or apt install python3 (Linux)"
command -v cmake >/dev/null 2>&1 || {
    warn "cmake not found."
    if [ "$OS" = "Darwin" ]; then
        ask "Install cmake via Homebrew? (Y/n) "
        read -r reply
        [[ "$reply" =~ ^[Nn]$ ]] && err "cmake is required for ESP-IDF" || brew install cmake
    else
        err "cmake is required. Install with: sudo apt install cmake"
    fi
}

log "Prerequisites OK"

# ─── Detect ESP32 serial port ────────────────────────────────────────────────

detect_serial_port() {
    if [ -n "$SERIAL_PORT" ]; then
        echo "$SERIAL_PORT"
        return
    fi

    if [ "$OS" = "Darwin" ]; then
        # macOS: look for common USB-serial adapters
        for p in /dev/cu.usbserial-* /dev/cu.SLAB_USBtoUART /dev/cu.wchusbserial*; do
            [ -e "$p" ] && echo "$p" && return
        done
    else
        # Linux: look for ttyUSB or ttyACM
        for p in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyACM0 /dev/ttyACM1; do
            [ -e "$p" ] && echo "$p" && return
        done
    fi

    echo ""
}

SERIAL_PORT=$(detect_serial_port)
if [ -z "$SERIAL_PORT" ]; then
    warn "No ESP32 serial port detected."
    ask "Enter the serial port path (e.g. /dev/cu.usbserial-110): "
    read -r SERIAL_PORT
    [ -z "$SERIAL_PORT" ] && err "Serial port is required. Plug in the ESP32 and try again."
fi

[ -e "$SERIAL_PORT" ] || err "Serial port $SERIAL_PORT does not exist. Is the ESP32 plugged in?"
log "ESP32 detected on $SERIAL_PORT"

# ─── Get WiFi credentials ───────────────────────────────────────────────────

if [ -z "$WIFI_SSID" ]; then
    echo ""
    echo -e "${YELLOW}The ESP32 needs your 2.4 GHz WiFi credentials to connect to your router.${NC}"
    echo -e "${YELLOW}It captures CSI (Channel State Information) from WiFi traffic on this network.${NC}"
    echo -e "${YELLOW}NOTE: ESP32 is 2.4 GHz ONLY — it cannot connect to 5 GHz networks.${NC}"
    echo ""
    ask "WiFi SSID (2.4 GHz network name): "
    read -r WIFI_SSID
    [ -z "$WIFI_SSID" ] && err "SSID is required"
fi

if [ -z "$WIFI_PASSWORD" ]; then
    ask "WiFi password: "
    read -rs WIFI_PASSWORD
    echo ""
    [ -z "$WIFI_PASSWORD" ] && err "Password is required"
fi

log "WiFi: $WIFI_SSID (password set)"

# ─── Step 1: Install ESP-IDF ────────────────────────────────────────────────

echo ""
log "═══ Step 1/4: ESP-IDF Toolchain ═══"

if [ -d "$ESP_IDF_DIR" ] && [ -f "$ESP_IDF_DIR/export.sh" ]; then
    log "ESP-IDF already installed at $ESP_IDF_DIR"
else
    log "Cloning ESP-IDF ($ESP_IDF_BRANCH)..."
    mkdir -p "$(dirname "$ESP_IDF_DIR")"
    git clone --recursive --branch "$ESP_IDF_BRANCH" \
        https://github.com/espressif/esp-idf.git "$ESP_IDF_DIR"

    log "Installing ESP-IDF tools (this takes 5-10 minutes)..."
    cd "$ESP_IDF_DIR"
    ./install.sh esp32
fi

# Source ESP-IDF environment
log "Loading ESP-IDF environment..."
source "$ESP_IDF_DIR/export.sh" 2>/dev/null || {
    # Fix common issue: psutil compiled for wrong arch
    if [ "$OS" = "Darwin" ]; then
        pip install --force-reinstall --no-binary psutil psutil 2>/dev/null || true
    fi
    source "$ESP_IDF_DIR/export.sh"
}

log "ESP-IDF ready ($(idf.py --version 2>/dev/null || echo 'version unknown'))"

# ─── Step 2: Clone esp-csi ──────────────────────────────────────────────────

echo ""
log "═══ Step 2/4: ESP-CSI Repository ═══"

if [ -d "$ESP_CSI_DIR" ]; then
    log "esp-csi already cloned at $ESP_CSI_DIR"
else
    log "Cloning esp-csi..."
    git clone https://github.com/espressif/esp-csi.git "$ESP_CSI_DIR"
fi

PROJ_DIR="$ESP_CSI_DIR/$FIRMWARE_DIR"
[ -d "$PROJ_DIR" ] || err "Firmware directory not found: $PROJ_DIR"

cd "$PROJ_DIR"
log "Working directory: $PROJ_DIR"

# ─── Patch firmware with Wi-Vi UART command + NVS WiFi support ─────────────

PATCH_DIR="$SCRIPT_DIR/firmware/csi_recv_router/main"
if [ -d "$PATCH_DIR" ]; then
    log "Patching firmware with Wi-Vi Sentinel extensions..."
    # Copy patched app_main.c (replaces example_connect with NVS WiFi loader)
    # and new source files (UART command handler, NVS WiFi helpers)
    cp "$PATCH_DIR/app_main.c" \
       "$PATCH_DIR/uart_cmd.c" "$PATCH_DIR/uart_cmd.h" \
       "$PATCH_DIR/wifi_nvs.c" "$PATCH_DIR/wifi_nvs.h" \
       "$PROJ_DIR/main/"

    # CMakeLists.txt uses SRC_DIRS "." so new .c files are auto-included.

    # Remove protocol_examples_common dependency from the project-level CMakeLists.txt
    # since our patched app_main.c replaces example_connect() with wifi_init_sta().
    PROJ_CMAKE="$PROJ_DIR/CMakeLists.txt"
    if [ -f "$PROJ_CMAKE" ] && grep -q "protocol_examples_common" "$PROJ_CMAKE"; then
        log "Removing protocol_examples_common dependency (replaced by wifi_nvs)..."
        if [ "$OS" = "Darwin" ]; then
            sed -i '' '/EXTRA_COMPONENT_DIRS.*protocol_examples_common/d' "$PROJ_CMAKE"
            sed -i '' '/protocol_examples_common/d' "$PROJ_CMAKE"
        else
            sed -i '/EXTRA_COMPONENT_DIRS.*protocol_examples_common/d' "$PROJ_CMAKE"
            sed -i '/protocol_examples_common/d' "$PROJ_CMAKE"
        fi
    fi

    log "Firmware patched"
else
    warn "Firmware patch files not found at $PATCH_DIR — skipping UART command support"
fi

# ─── Step 3: Configure firmware ─────────────────────────────────────────────

echo ""
log "═══ Step 3/4: Configure Firmware ═══"

# Set WiFi credentials via sdkconfig.defaults (avoids interactive menuconfig)
log "Setting WiFi credentials in sdkconfig..."

# Ensure CSI is enabled and WiFi creds are set
# We write to sdkconfig.defaults and let the build pick them up
cat > sdkconfig.defaults.user << EOF
# Wi-Vi Sentinel ESP32 CSI Configuration
# Generated by setup_esp32.sh

# WiFi credentials
CONFIG_EXAMPLE_WIFI_SSID="$WIFI_SSID"
CONFIG_EXAMPLE_WIFI_PASSWORD="$WIFI_PASSWORD"

# Enable CSI
CONFIG_ESP_WIFI_CSI_ENABLED=y

# Disable AMPDU TX for cleaner CSI
CONFIG_ESP_WIFI_AMPDU_TX_ENABLED=n

# Console baud rate — must match ESP32_BAUD_RATE in .env
CONFIG_ESP_CONSOLE_UART_BAUDRATE=921600
CONFIG_ESPTOOLPY_MONITOR_BAUD=921600

# Performance
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
EOF

# Remove stale sdkconfig to force regeneration from defaults
rm -f sdkconfig

# Handle ESP-IDF version compatibility for esp_csi_gain_ctrl
IDF_VER=$(python3 -c "import re; v=open('$ESP_IDF_DIR/version.txt').read().strip() if __import__('os').path.exists('$ESP_IDF_DIR/version.txt') else ''; print(v[:3] if v else '')" 2>/dev/null || echo "")
GAIN_CTRL_DIR="$PROJ_DIR/managed_components/espressif__esp_csi_gain_ctrl"

if [ -d "$GAIN_CTRL_DIR" ] && [ -n "$IDF_VER" ]; then
    # Check if the IDF major.minor version directory exists
    if [ ! -d "$GAIN_CTRL_DIR/$IDF_VER" ]; then
        # Find the closest available version
        LATEST_VER=$(ls -d "$GAIN_CTRL_DIR"/[0-9]* 2>/dev/null | sort -V | tail -1)
        if [ -n "$LATEST_VER" ]; then
            LATEST_BASE=$(basename "$LATEST_VER")
            log "esp_csi_gain_ctrl: copying $LATEST_BASE → $IDF_VER (version compatibility)"
            cp -r "$LATEST_VER" "$GAIN_CTRL_DIR/$IDF_VER"
        fi
    fi
fi

log "Firmware configured for SSID: $WIFI_SSID"

# ─── Step 4: Build and flash ────────────────────────────────────────────────

echo ""
log "═══ Step 4/4: Build & Flash ═══"

log "Building firmware (first build takes 3-5 minutes)..."
idf.py set-target esp32

# First build attempt — may fail if managed_components need version patching
if ! idf.py build 2>/dev/null; then
    warn "First build attempt failed — checking for version compatibility issues..."

    # The managed_components directory is created during the first build attempt.
    # Re-run the esp_csi_gain_ctrl version fix now that it exists.
    GAIN_CTRL_DIR="$PROJ_DIR/managed_components/espressif__esp_csi_gain_ctrl"
    if [ -d "$GAIN_CTRL_DIR" ] && [ -n "$IDF_VER" ]; then
        if [ ! -d "$GAIN_CTRL_DIR/$IDF_VER" ]; then
            LATEST_VER=$(ls -d "$GAIN_CTRL_DIR"/[0-9]* 2>/dev/null | sort -V | tail -1)
            if [ -n "$LATEST_VER" ]; then
                LATEST_BASE=$(basename "$LATEST_VER")
                log "esp_csi_gain_ctrl: copying $LATEST_BASE → $IDF_VER (version compatibility)"
                cp -r "$LATEST_VER" "$GAIN_CTRL_DIR/$IDF_VER"
            fi
        fi
    fi

    log "Retrying build..."
    idf.py build
fi

log "Flashing firmware to $SERIAL_PORT..."
idf.py flash -p "$SERIAL_PORT"

# ─── Verify ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ESP32 CSI Firmware — Flash Complete${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${YELLOW}Hardware:${NC}     ESP32-DevKitC-32E"
echo -e "  ${YELLOW}Firmware:${NC}     csi_recv_router (Espressif esp-csi)"
echo -e "  ${YELLOW}WiFi SSID:${NC}    $WIFI_SSID (2.4 GHz)"
echo -e "  ${YELLOW}Serial port:${NC}  $SERIAL_PORT"
echo -e "  ${YELLOW}Baud rate:${NC}    921600"
echo ""
echo -e "  ${CYAN}To verify CSI data output:${NC}"
echo -e "    source $ESP_IDF_DIR/export.sh"
echo -e "    cd $PROJ_DIR"
echo -e "    idf.py monitor -p $SERIAL_PORT"
echo ""
echo -e "  You should see WiFi connection logs followed by CSI_DATA lines."
echo -e "  Press the ${YELLOW}EN/RST${NC} button on the ESP32 if no output appears."
echo -e "  Quit monitor with ${YELLOW}Ctrl+]${NC}"
echo ""
echo -e "  ${CYAN}To deploy to Raspberry Pi:${NC}"
echo -e "    1. Unplug ESP32 from this machine"
echo -e "    2. Plug ESP32 into Pi's USB port"
echo -e "    3. On the Pi, set .env: CSI_SOURCE=esp32"
echo -e "    4. Run: python server.py"
echo ""
echo -e "  ${CYAN}Pi .env settings:${NC}"
echo -e "    CSI_SOURCE=esp32"
echo -e "    ESP32_SERIAL_PORT=/dev/ttyUSB0"
echo -e "    ESP32_BAUD_RATE=921600"
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
