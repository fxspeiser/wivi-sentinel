#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Wi-Vi Sentinel — Raspberry Pi Full Setup Script
# ═══════════════════════════════════════════════════════════════════════════════
#
# Installs and configures everything needed to run Wi-Vi Sentinel on a
# Raspberry Pi 4 with an ESP32 CSI sensor over USB.
#
# Fully non-interactive when environment variables are pre-set.
# Prompts for any required value not already in the environment.
#
# USAGE (human):
#   curl -fsSL https://raw.githubusercontent.com/YOUR_USER/wivi-sentinel/main/setup_pi.sh | bash
#   # or clone first, then:
#   chmod +x setup_pi.sh && ./setup_pi.sh
#
# USAGE (pre-configured / AI agents):
#   export WIVI_REPO="https://github.com/YOUR_USER/wivi-sentinel.git"
#   export WIVI_DIR="$HOME/wivi-sentinel"
#   export WIVI_USER="$USER"
#   export FLASK_PORT=5555
#   export RUVIEW_PORT=3100
#   export CSI_SOURCE=esp32
#   export ESP32_SERIAL_PORT=/dev/ttyUSB0
#   export ESP32_BAUD_RATE=921600
#   export RUVIEW_ENABLED=true
#   ./setup_pi.sh
#
# Optional env vars (skip prompts entirely):
#   WIFI_SSID         — used to update ESP32 WiFi via API after startup
#   WIFI_PASSWORD     — (same)
#   RUVIEW_ENABLED    — set false to skip Docker / RuView install
#   SKIP_DOCKER       — set true to skip Docker install
#   SKIP_SYSTEMD      — set true to skip systemd service creation
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[SENTINEL]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC}     $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}     $*"; }
err()  { echo -e "${RED}[ERROR]${NC}    $*"; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── Defaults ─────────────────────────────────────────────────────────────────

WIVI_REPO="${WIVI_REPO:-https://github.com/YOUR_USER/wivi-sentinel.git}"
WIVI_DIR="${WIVI_DIR:-$HOME/wivi-sentinel}"
WIVI_USER="${WIVI_USER:-$USER}"
FLASK_PORT="${FLASK_PORT:-5555}"
VITE_PORT="${VITE_PORT:-3000}"
RUVIEW_PORT="${RUVIEW_PORT:-3100}"
CSI_SOURCE="${CSI_SOURCE:-esp32}"
ESP32_SERIAL_PORT="${ESP32_SERIAL_PORT:-/dev/ttyUSB0}"
ESP32_BAUD_RATE="${ESP32_BAUD_RATE:-921600}"
RUVIEW_ENABLED="${RUVIEW_ENABLED:-true}"
SKIP_DOCKER="${SKIP_DOCKER:-false}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-false}"

# ── Preflight ─────────────────────────────────────────────────────────────────

step "Preflight checks"

# Detect architecture
ARCH=$(uname -m)
info "Architecture: $ARCH"
info "OS: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2 || uname -s)"

# Warn if not running on Pi (non-fatal)
if ! grep -qiE "raspberry|BCM" /proc/cpuinfo 2>/dev/null; then
    warn "This doesn't look like a Raspberry Pi — continuing anyway"
fi

# ── System dependencies ───────────────────────────────────────────────────────

step "Installing system dependencies"

sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    libgfortran5 \
    libopenblas0 \
    libpcap-dev \
    avahi-daemon \
    iw \
    net-tools \
    ca-certificates \
    gnupg \
    lsb-release \
    2>/dev/null

sudo systemctl enable --now avahi-daemon 2>/dev/null || true

log "System dependencies installed"

# ── Docker ────────────────────────────────────────────────────────────────────

if [ "$SKIP_DOCKER" != "true" ] && [ "$RUVIEW_ENABLED" = "true" ]; then
    step "Installing Docker"

    if command -v docker &>/dev/null; then
        info "Docker already installed: $(docker --version)"
    else
        info "Downloading Docker install script..."
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        sudo sh /tmp/get-docker.sh
        rm -f /tmp/get-docker.sh
        log "Docker installed"
    fi

    # Add current user to docker group so we can run without sudo
    if ! groups "$WIVI_USER" | grep -q docker; then
        sudo usermod -aG docker "$WIVI_USER"
        warn "Added $WIVI_USER to docker group — takes effect on next login"
        warn "If Docker commands fail, log out and back in, then re-run: ./start.sh"
        # For this session, use sg docker to inherit the group
        DOCKER_CMD="sg docker -c docker"
    else
        DOCKER_CMD="docker"
    fi

    # Pull RuView image in the background so it's ready when start.sh runs
    info "Pre-pulling RuView Docker image (background)..."
    $DOCKER_CMD pull ruvnet/wifi-densepose:latest &>/dev/null &
    RUVIEW_PULL_PID=$!
    log "RuView image pull started in background (PID $RUVIEW_PULL_PID)"
fi

# ── Clone or update repository ────────────────────────────────────────────────

step "Deploying Wi-Vi Sentinel"

if [ -d "$WIVI_DIR/.git" ]; then
    info "Repository exists — pulling latest changes..."
    git -C "$WIVI_DIR" pull --rebase 2>/dev/null || warn "git pull failed, continuing with existing files"
elif [ -d "$WIVI_DIR" ] && [ -f "$WIVI_DIR/server.py" ]; then
    info "Found existing deployment in $WIVI_DIR (no git) — using as-is"
else
    info "Cloning from $WIVI_REPO..."
    git clone "$WIVI_REPO" "$WIVI_DIR" || {
        warn "git clone failed — creating directory and expecting manual file copy"
        mkdir -p "$WIVI_DIR"
    }
fi

cd "$WIVI_DIR"

# ── Python virtual environment ────────────────────────────────────────────────

step "Setting up Python environment"

if [ ! -d "$WIVI_DIR/venv" ]; then
    python3 -m venv "$WIVI_DIR/venv"
    log "Created virtualenv at $WIVI_DIR/venv"
fi

source "$WIVI_DIR/venv/bin/activate"

pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
log "Python dependencies installed"

deactivate

# ── Configure .env ────────────────────────────────────────────────────────────

step "Configuring environment"

ENV_FILE="$WIVI_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    info "Existing .env found — updating only missing values"
else
    cp "$WIVI_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
    info "Created .env from template"
fi

# Helper: set or replace a key in .env
set_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

set_env FLASK_PORT     "$FLASK_PORT"
set_env VITE_PORT      "$VITE_PORT"
set_env CSI_SOURCE     "$CSI_SOURCE"
set_env ESP32_SERIAL_PORT "$ESP32_SERIAL_PORT"
set_env ESP32_BAUD_RATE   "$ESP32_BAUD_RATE"
set_env RUVIEW_PORT    "$RUVIEW_PORT"
set_env RUVIEW_URL     "http://localhost:${RUVIEW_PORT}"
set_env RUVIEW_ENABLED "$RUVIEW_ENABLED"

log ".env configured"
info "  CSI_SOURCE=$CSI_SOURCE"
info "  ESP32_SERIAL_PORT=$ESP32_SERIAL_PORT"
info "  FLASK_PORT=$FLASK_PORT"
info "  RUVIEW_PORT=$RUVIEW_PORT"

# ── ESP32 serial port permissions ─────────────────────────────────────────────

step "Configuring ESP32 serial access"

if [ -e "$ESP32_SERIAL_PORT" ]; then
    log "ESP32 detected at $ESP32_SERIAL_PORT"
    sudo usermod -aG dialout "$WIVI_USER" 2>/dev/null || true
    sudo chmod 666 "$ESP32_SERIAL_PORT" 2>/dev/null || true
    log "Serial port permissions set"
else
    warn "ESP32 not found at $ESP32_SERIAL_PORT"
    warn "Plug the ESP32 into a USB port and verify: ls /dev/ttyUSB*"
fi

# ── Data directory ────────────────────────────────────────────────────────────

step "Setting up data directory"

mkdir -p "$WIVI_DIR/data"
if [ ! -f "$WIVI_DIR/data/profiles.json" ]; then
    echo '{}' > "$WIVI_DIR/data/profiles.json"
    log "Created empty profiles.json"
fi

# ── Check for pre-built dashboard ─────────────────────────────────────────────

step "Dashboard"

if [ -f "$WIVI_DIR/dist/index.html" ]; then
    log "Pre-built dashboard found at dist/index.html"
else
    warn "dist/index.html not found"
    warn "Build the dashboard on a machine with Node >=18:"
    warn "  npm install && npm run build"
    warn "Then copy dist/ to $WIVI_DIR/dist/ on this Pi:"
    warn "  rsync -av dist/ ${WIVI_USER}@$(hostname -I | awk '{print $1}'):$WIVI_DIR/dist/"
    warn "Falling back to legacy CDN dashboard (index.legacy.html) until dist/ is available"
fi

# ── Systemd service ───────────────────────────────────────────────────────────

if [ "$SKIP_SYSTEMD" != "true" ]; then
    step "Creating systemd service"

    ACTIVATE_CMD="source $WIVI_DIR/venv/bin/activate"
    START_CMD="python3 $WIVI_DIR/server.py"

    sudo tee /etc/systemd/system/wivi-sentinel.service > /dev/null << EOF
[Unit]
Description=Wi-Vi Sentinel — WiFi CSI Biometric Detection
Documentation=https://github.com/YOUR_USER/wivi-sentinel
After=network-online.target docker.service
Wants=network-online.target
Requires=wivi-ruview.service

[Service]
Type=simple
User=${WIVI_USER}
WorkingDirectory=${WIVI_DIR}
EnvironmentFile=${WIVI_DIR}/.env
ExecStartPre=/bin/sleep 3
ExecStart=${WIVI_DIR}/venv/bin/python3 ${WIVI_DIR}/server.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    if [ "$RUVIEW_ENABLED" = "true" ] && [ "$SKIP_DOCKER" != "true" ]; then
        sudo tee /etc/systemd/system/wivi-ruview.service > /dev/null << EOF
[Unit]
Description=Wi-Vi Sentinel — RuView Pose Estimation (Docker)
Documentation=https://github.com/ruvnet/RuView
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
User=${WIVI_USER}
Restart=on-failure
RestartSec=10
ExecStartPre=-/usr/bin/docker rm -f wivi-ruview
ExecStart=/usr/bin/docker run --rm \
    --name wivi-ruview \
    -p ${RUVIEW_PORT}:3000 \
    -p 5005:5005/udp \
    -e CSI_SOURCE=simulated \
    ruvnet/wifi-densepose:latest
ExecStop=/usr/bin/docker stop wivi-ruview
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
        log "Created wivi-ruview.service"
    fi

    sudo systemctl daemon-reload
    sudo systemctl enable wivi-sentinel.service 2>/dev/null || true
    [ "$RUVIEW_ENABLED" = "true" ] && sudo systemctl enable wivi-ruview.service 2>/dev/null || true

    log "Systemd services enabled (start on boot)"
fi

# ── Convenience CLI commands ──────────────────────────────────────────────────

step "Installing CLI commands"

sudo tee /usr/local/bin/wivi-start > /dev/null << EOF
#!/usr/bin/env bash
# Start Wi-Vi Sentinel (and RuView if enabled)
cd ${WIVI_DIR}
exec ./start.sh "\$@"
EOF

sudo tee /usr/local/bin/wivi-stop > /dev/null << EOF
#!/usr/bin/env bash
echo "Stopping Wi-Vi Sentinel..."
sudo systemctl stop wivi-sentinel 2>/dev/null || true
sudo systemctl stop wivi-ruview   2>/dev/null || true
docker stop wivi-ruview 2>/dev/null || true
echo "Stopped."
EOF

sudo tee /usr/local/bin/wivi-status > /dev/null << STATUSEOF
#!/usr/bin/env bash
echo "═══════════════════════════════════════════"
echo "  Wi-Vi Sentinel Status"
echo "═══════════════════════════════════════════"
echo ""
echo "── Services ──"
systemctl is-active wivi-sentinel 2>/dev/null && echo "  sentinel: RUNNING" || echo "  sentinel: STOPPED"
systemctl is-active wivi-ruview   2>/dev/null && echo "  ruview:   RUNNING" || echo "  ruview:   STOPPED"
echo ""
echo "── ESP32 ──"
ls /dev/ttyUSB* 2>/dev/null || echo "  No USB serial devices found"
echo ""
echo "── Dashboard ──"
PI_IP=\$(hostname -I | awk '{print \$1}')
echo "  http://\${PI_IP}:${FLASK_PORT}"
echo "  http://\${PI_IP}:${FLASK_PORT}/api/status"
echo ""
echo "── RuView ──"
echo "  http://\${PI_IP}:${RUVIEW_PORT}/ui/observatory.html"
echo ""
echo "── Logs ──"
echo "  journalctl -u wivi-sentinel -f"
echo "  journalctl -u wivi-ruview -f"
STATUSEOF

sudo chmod +x /usr/local/bin/wivi-start \
              /usr/local/bin/wivi-stop  \
              /usr/local/bin/wivi-status

log "Commands installed: wivi-start, wivi-stop, wivi-status"

# ── Wait for RuView image pull (if started) ───────────────────────────────────

if [ -n "${RUVIEW_PULL_PID:-}" ]; then
    step "Waiting for RuView image pull to complete"
    wait "$RUVIEW_PULL_PID" && log "RuView image ready" || warn "RuView image pull may have failed — will retry on first start"
fi

# ── Auto-start ────────────────────────────────────────────────────────────────

step "Starting services"

if [ "$SKIP_SYSTEMD" = "true" ]; then
    info "SKIP_SYSTEMD=true — not starting services automatically"
    info "Run manually: cd $WIVI_DIR && ./start.sh"
else
    if [ "$RUVIEW_ENABLED" = "true" ] && [ "$SKIP_DOCKER" != "true" ]; then
        sudo systemctl start wivi-ruview 2>/dev/null || warn "wivi-ruview failed to start (check: journalctl -u wivi-ruview)"
    fi

    if [ -f "$WIVI_DIR/dist/index.html" ] || [ -f "$WIVI_DIR/index.legacy.html" ]; then
        sudo systemctl start wivi-sentinel 2>/dev/null || warn "wivi-sentinel failed to start (check: journalctl -u wivi-sentinel)"
    else
        warn "Skipping sentinel start — deploy dist/ first"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<pi-ip>")

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Wi-Vi Sentinel Setup Complete${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Dashboard:${NC}  http://${PI_IP}:${FLASK_PORT}"
echo -e "  ${BOLD}API:${NC}        http://${PI_IP}:${FLASK_PORT}/api/status"
[ "$RUVIEW_ENABLED" = "true" ] && \
echo -e "  ${BOLD}RuView:${NC}     http://${PI_IP}:${RUVIEW_PORT}/ui/observatory.html"
echo ""
echo -e "  ${YELLOW}CLI Commands:${NC}"
echo -e "  ${CYAN}wivi-start${NC}   — start all services"
echo -e "  ${CYAN}wivi-stop${NC}    — stop all services"
echo -e "  ${CYAN}wivi-status${NC}  — show status, IPs, and log commands"
echo ""

if ! [ -f "$WIVI_DIR/dist/index.html" ]; then
    echo -e "  ${YELLOW}⚠ ACTION REQUIRED:${NC} Deploy the pre-built dashboard:"
    echo -e "  On your Mac: ${CYAN}npm run build${NC}"
    echo -e "  Then:        ${CYAN}rsync -av dist/ ${WIVI_USER}@${PI_IP}:${WIVI_DIR}/dist/${NC}"
    echo ""
fi

if ! [ -e "$ESP32_SERIAL_PORT" ]; then
    echo -e "  ${YELLOW}⚠ ACTION REQUIRED:${NC} ESP32 not detected at ${ESP32_SERIAL_PORT}"
    echo -e "  Plug in the ESP32 and verify: ${CYAN}ls /dev/ttyUSB*${NC}"
    echo ""
fi

echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
