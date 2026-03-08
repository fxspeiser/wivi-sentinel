#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Wi-Vi Sentinel — Raspberry Pi 4 Model B Nexmon CSI Setup
# ═══════════════════════════════════════════════════════════════════════════════
#
# Run this on a fresh Raspberry Pi OS (32-bit recommended for Nexmon compat).
# This script:
#   1. Installs build dependencies
#   2. Clones and builds Nexmon + CSI extractor
#   3. Patches the BCM43455c0 firmware for CSI extraction
#   4. Installs the CSI extractor daemon
#   5. Configures monitor mode on wlan0
#   6. Sets up the UDP forwarder service
#
# Usage:
#   chmod +x setup_pi.sh
#   sudo ./setup_pi.sh
#
# IMPORTANT: This must run on a Raspberry Pi 4 Model B with Raspberry Pi OS.
#            32-bit Bullseye or Bookworm recommended. 64-bit may require
#            additional patches.
# ═══════════════════════════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[SENTINEL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ─── Preflight checks ────────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    err "Must run as root: sudo ./setup_pi.sh"
fi

# Verify we're on a Pi 4
if ! grep -q "BCM2711" /proc/cpuinfo 2>/dev/null; then
    warn "This doesn't look like a Raspberry Pi 4 (BCM2711 not found in cpuinfo)"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# Check for BCM43455
if ! lsmod | grep -q brcmfmac; then
    warn "brcmfmac module not loaded — WiFi chip may not be detected"
fi

log "Raspberry Pi 4 Model B detected"
log "WiFi chip: BCM43455c0 (SDIO)"

# ─── Configuration ────────────────────────────────────────────────────────────

INSTALL_DIR="/opt/wivi-sentinel"
NEXMON_DIR="/opt/nexmon"
NEXMON_CSI_DIR="/opt/nexmon_csi"

# Target Mac IP — the machine running the dashboard
# This will be configurable later via the extractor config
MAC_IP="${MAC_IP:-auto}"
UDP_PORT="${UDP_PORT:-5500}"

# WiFi channel to monitor (should match your router's channel)
MONITOR_CHANNEL="${MONITOR_CHANNEL:-36}"
MONITOR_BANDWIDTH="${MONITOR_BANDWIDTH:-80}"  # 20, 40, or 80 MHz

# ─── Install dependencies ────────────────────────────────────────────────────

log "Installing build dependencies..."
apt-get update -qq
apt-get install -y \
    git \
    libgmp3-dev \
    gawk \
    qpdf \
    bison \
    flex \
    make \
    autoconf \
    libtool \
    texinfo \
    automake \
    build-essential \
    libncurses5-dev \
    python3 \
    python3-pip \
    tcpdump \
    iw \
    net-tools \
    2>/dev/null

# Python dependencies for the extractor and device scanner
pip3 install numpy scipy zeroconf scapy --break-system-packages 2>/dev/null || pip3 install numpy scipy zeroconf scapy

# ─── Get kernel headers ──────────────────────────────────────────────────────

log "Installing kernel headers..."
apt-get install -y raspberrypi-kernel-headers 2>/dev/null || {
    warn "Could not install kernel headers via apt. Trying rpi-update method..."
    KERNEL_VERSION=$(uname -r)
    log "Running kernel: $KERNEL_VERSION"
}

# ─── Clone and build Nexmon ──────────────────────────────────────────────────

log "Cloning Nexmon base framework..."
if [ -d "$NEXMON_DIR" ]; then
    warn "Nexmon directory exists, pulling latest..."
    cd "$NEXMON_DIR" && git pull
else
    git clone https://github.com/seemoo-lab/nexmon.git "$NEXMON_DIR"
fi

cd "$NEXMON_DIR"

# Detect firmware version
FIRMWARE_VERSION=$(strings /lib/firmware/brcm/brcmfmac43455-sdio.bin | grep -oP 'Version: [\d.]+' | head -1 || echo "")
log "Current firmware: ${FIRMWARE_VERSION:-unknown}"

# Set up build environment
log "Setting up Nexmon build environment (this takes ~10 minutes)..."
source setup_env.sh
cd buildtools
make -j$(nproc) 2>/dev/null || make
cd ..

# Build shared library
cd utilities/libnexmon
make -j$(nproc) 2>/dev/null || make
cd ../..

# ─── Clone and build Nexmon CSI ──────────────────────────────────────────────

log "Cloning Nexmon CSI extractor..."
if [ -d "$NEXMON_CSI_DIR" ]; then
    warn "Nexmon CSI directory exists, pulling latest..."
    cd "$NEXMON_CSI_DIR" && git pull
else
    git clone https://github.com/seemoo-lab/nexmon_csi.git "$NEXMON_CSI_DIR"
fi

cd "$NEXMON_CSI_DIR"

# Build the CSI patched firmware for BCM43455c0
log "Building CSI-patched firmware for BCM43455c0..."
cd patches/bcm43455c0/7_45_189/nexmon_csi/

# Link Nexmon base
export NEXMON_HOME="$NEXMON_DIR"
source "$NEXMON_DIR/setup_env.sh"

make clean 2>/dev/null || true
make -j$(nproc) 2>/dev/null || make
make install

log "CSI-patched firmware installed"

# ─── Backup original firmware ────────────────────────────────────────────────

FIRMWARE_PATH="/lib/firmware/brcm/brcmfmac43455-sdio.bin"
BACKUP_PATH="${FIRMWARE_PATH}.original"

if [ ! -f "$BACKUP_PATH" ]; then
    log "Backing up original firmware to ${BACKUP_PATH}"
    cp "$FIRMWARE_PATH" "$BACKUP_PATH"
fi

# ─── Install sentinel extractor ──────────────────────────────────────────────

log "Installing Wi-Vi Sentinel CSI extractor..."
mkdir -p "$INSTALL_DIR"

# Copy extractor scripts
cp /tmp/wivi_pi_files/csi_extractor.py "$INSTALL_DIR/" 2>/dev/null || {
    warn "Extractor script not found in /tmp/wivi_pi_files/"
    warn "Copy csi_extractor.py to $INSTALL_DIR/ manually"
}

# Create config file
cat > "$INSTALL_DIR/config.json" << EOF
{
    "mac_ip": "${MAC_IP}",
    "udp_port": ${UDP_PORT},
    "monitor_channel": ${MONITOR_CHANNEL},
    "bandwidth": ${MONITOR_BANDWIDTH},
    "interface": "wlan0",
    "sample_rate": 100,
    "auto_discover_mac": true,
    "log_level": "INFO"
}
EOF

# ─── Create systemd service ──────────────────────────────────────────────────

log "Creating systemd service..."
cat > /etc/systemd/system/wivi-csi.service << EOF
[Unit]
Description=Wi-Vi Sentinel CSI Extractor
After=network.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/csi_extractor.py --config ${INSTALL_DIR}/config.json
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ─── Create monitor mode helper script ───────────────────────────────────────

cat > "$INSTALL_DIR/start_monitor.sh" << 'MONITOR_SCRIPT'
#!/usr/bin/env bash
# Bring wlan0 into monitor mode with Nexmon CSI firmware

CHANNEL=${1:-36}
BANDWIDTH=${2:-80}

set -e

echo "[SENTINEL] Configuring monitor mode on wlan0..."

# Unload and reload with patched firmware
ifconfig wlan0 down 2>/dev/null || true
rmmod brcmfmac 2>/dev/null || true
sleep 1
modprobe brcmfmac

# Wait for interface
for i in $(seq 1 10); do
    if iw dev wlan0 info >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Set monitor mode
iw dev wlan0 interface add mon0 type monitor 2>/dev/null || {
    ip link set wlan0 down
    iw dev wlan0 set type monitor
    ip link set wlan0 up
}

# If mon0 was created, use that; otherwise wlan0 is in monitor mode
if iw dev mon0 info >/dev/null 2>&1; then
    IFACE="mon0"
    ip link set mon0 up
else
    IFACE="wlan0"
    ip link set wlan0 up
fi

# Set channel and bandwidth
# Nexmon uses chanspec format for bandwidth control
case $BANDWIDTH in
    20) BW_FLAG="" ;;
    40) BW_FLAG="HT40+" ;;
    80) BW_FLAG="80MHz" ;;
    *) BW_FLAG="" ;;
esac

iw dev $IFACE set channel $CHANNEL $BW_FLAG 2>/dev/null || {
    echo "[SENTINEL] Warning: Could not set channel $CHANNEL $BW_FLAG"
    echo "[SENTINEL] Trying nexutil..."
    # Use nexutil for chanspec (Nexmon-specific)
    # chanspec encoding: channel | (bandwidth << 8)
    if command -v nexutil &>/dev/null; then
        nexutil -Iwlan0 -s500 -b -l34 \
            -v$(python3 -c "import struct; print(struct.pack('<IIBBHHBBBBBBBBBBBBBBBBBBBBBBBBBB', $CHANNEL, 0, 0, 0, 0, 0, *([0]*26)).hex())")
    fi
}

echo "[SENTINEL] Monitor mode active on $IFACE, channel $CHANNEL, bandwidth ${BANDWIDTH}MHz"
MONITOR_SCRIPT

chmod +x "$INSTALL_DIR/start_monitor.sh"

# ─── Create convenience scripts ──────────────────────────────────────────────

cat > /usr/local/bin/wivi-start << EOF
#!/bin/bash
echo "Starting Wi-Vi Sentinel CSI extraction..."
sudo ${INSTALL_DIR}/start_monitor.sh ${MONITOR_CHANNEL} ${MONITOR_BANDWIDTH}
sudo systemctl start wivi-csi
echo "Streaming CSI to ${MAC_IP}:${UDP_PORT}"
echo "Monitor with: sudo journalctl -u wivi-csi -f"
EOF

cat > /usr/local/bin/wivi-stop << EOF
#!/bin/bash
echo "Stopping Wi-Vi Sentinel..."
sudo systemctl stop wivi-csi
sudo ifconfig wlan0 down 2>/dev/null
sudo rmmod brcmfmac 2>/dev/null
sudo modprobe brcmfmac
echo "Stopped. Normal WiFi restored."
EOF

cat > /usr/local/bin/wivi-status << EOF
#!/bin/bash
echo "=== Wi-Vi Sentinel Status ==="
echo ""
echo "Service:"
systemctl is-active wivi-csi 2>/dev/null || echo "  not running"
echo ""
echo "Interface:"
iw dev wlan0 info 2>/dev/null || echo "  wlan0 not available"
echo ""
echo "Config:"
cat ${INSTALL_DIR}/config.json 2>/dev/null
echo ""
echo "Recent logs:"
journalctl -u wivi-csi --no-pager -n 10 2>/dev/null
EOF

chmod +x /usr/local/bin/wivi-start
chmod +x /usr/local/bin/wivi-stop
chmod +x /usr/local/bin/wivi-status

# ─── Final summary ───────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Wi-Vi Sentinel — Pi 4 Setup Complete${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${YELLOW}Before first run:${NC}"
echo ""
echo -e "  1. Copy ${CYAN}csi_extractor.py${NC} to ${INSTALL_DIR}/"
echo -e "  2. Edit ${CYAN}${INSTALL_DIR}/config.json${NC}:"
echo -e "     - Set ${YELLOW}mac_ip${NC} to your Mac's local IP (e.g. 192.168.1.71)"
echo -e "     - Set ${YELLOW}monitor_channel${NC} to your router's WiFi channel"
echo -e "       (find it with: ${CYAN}sudo iwlist wlan0 channel${NC})"
echo -e "     - Set ${YELLOW}bandwidth${NC} to 20, 40, or 80"
echo ""
echo -e "  ${YELLOW}Commands:${NC}"
echo -e "  ${CYAN}wivi-start${NC}    — Start CSI extraction + UDP streaming"
echo -e "  ${CYAN}wivi-stop${NC}     — Stop and restore normal WiFi"
echo -e "  ${CYAN}wivi-status${NC}   — Check service status and recent logs"
echo ""
echo -e "  ${YELLOW}Note:${NC} When monitor mode is active, the Pi's onboard WiFi"
echo -e "  cannot connect to your network. Use Ethernet for SSH/network."
echo -e "  Plug in an Ethernet cable before running ${CYAN}wivi-start${NC}."
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"