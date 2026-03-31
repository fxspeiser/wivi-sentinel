# Wi-Vi Sentinel

WiFi CSI biometric detection, classification, and tracking.
Passive through-wall sensing using an ESP32 + Raspberry Pi 4.

```
                         WiFi signals (2.4 GHz)
  [Your Router] ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
       │                                      │
       │                            (bodies disturb signal)
       │                                      │
       │         [ESP32-DevKitC-32E] ◄─ ─ ─ ─ ┘
       │              │  Captures CSI from every WiFi packet
       │              │  USB serial @ 921600 baud
       │              │
  [Raspberry Pi 4] ◄──┘
       │  server.py — signal processing, classification
       │  Species / sex / direction / device correlation
       │
       └──► http://<pi-ip>:5555  (dashboard + API)
```

---

## Hardware Required

| Item | Notes |
|---|---|
| **ESP32-DevKitC-32E** | CSI capture (~$10). Must be the 32E variant — S3/C6 don't work with this firmware |
| **Raspberry Pi 4 Model B** | Any RAM size. Runs server |
| **USB-A to Micro-USB cable** | Data cable (not charge-only) — connects ESP32 to Pi |
| **Micro SD card** (16 GB+) | For Pi OS |
| **USB-C power supply** | For Pi (≥3A recommended) |
| **Ethernet cable** | Strongly recommended — Pi WiFi is free but router DHCP can be unreliable |

> The Pi's WiFi card is unused — the ESP32 handles CSI capture. Connect the Pi via Ethernet for a stable server connection.

---

## Quick Start

### Step 1 — Flash the ESP32 (on your Mac or Linux machine, not the Pi)

The ESP32 must be flashed from a machine with a USB port running macOS or Linux (not the Pi itself).

```bash
git clone https://github.com/YOUR_USER/wivi-sentinel.git
cd wivi-sentinel

# With prompts:
./setup_esp32.sh

# Or fully automated:
WIFI_SSID="YourNetwork" WIFI_PASSWORD="YourPassword" ./setup_esp32.sh
```

Installs ESP-IDF, builds and flashes the `csi_recv_router` firmware.
See [ESP32 Details](#esp32-setup-details) for manual steps.

### Step 2 — Flash Raspberry Pi OS

Use **Raspberry Pi Imager**. In the settings ("gear" icon):
- Enable SSH
- Set hostname (e.g. `raspberrypi`)
- Set username/password
- Configure WiFi (or plan to use Ethernet)

Boot the Pi, then find its IP address:
```bash
# From your router's admin page (look for "raspberrypi" in DHCP leases)
# Or from another machine on the same network:
ping raspberrypi.local

# Or on the Pi itself (connect a monitor temporarily):
hostname -I
```

Confirm SSH works:
```bash
ssh <your-pi-user>@<pi-ip>
```

### Step 3 — Run the setup script on the Pi

SSH into the Pi, then:
```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_USER/wivi-sentinel/main/setup_pi.sh | bash

# Or with pre-set values (no prompts):
WIVI_REPO="https://github.com/YOUR_USER/wivi-sentinel.git" \
CSI_SOURCE=esp32 \
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USER/wivi-sentinel/main/setup_pi.sh)
```

This clones the repo, installs Python dependencies, and sets up the systemd service.

### Step 4 — Plug in the ESP32 and start

Move the ESP32 from your Mac to a USB port on the Pi, then:

```bash
# On the Pi:
~/wivi-sentinel/start.sh
```

Open `http://<pi-ip>:5555` in your browser.

---

## Deploying Updates

When a new version is published to GitHub, update the Pi with:

```bash
cd ~/wivi-sentinel && git pull && ./start.sh restart
```

`dist/` (the pre-built dashboard) is committed to the repo so the Pi always gets the latest UI on pull.

---

## 🤖 AI Agent Setup Guide

> **For Claude, Codex, Cursor, and other AI coding assistants.**
> This section is written as executable instructions. Follow each block in sequence.
> Verify each step before proceeding. Zero human interaction required after you have:
> - Pi IP address
> - Pi SSH username + password
> - WiFi SSID + password (for ESP32)
> - Path to this repository on the host machine

---

### Context you need before starting

```
PI_IP=<from user>             # find with: ping raspberrypi.local or check router DHCP
PI_USER=<from user>           # the username set in Raspberry Pi Imager
PI_PASS=<from user>           # for sudo operations
WIFI_SSID=<from user>         # ESP32 must join a 2.4 GHz network
WIFI_PASSWORD=<from user>
```

---

### Phase 1 — Flash the ESP32 (run on Mac/Linux, not the Pi)

Clone the repo on the machine connected to the ESP32 via USB, then flash:

**1.1 Flash firmware**
```bash
git clone https://github.com/YOUR_USER/wivi-sentinel.git
cd wivi-sentinel
WIFI_SSID="$WIFI_SSID" WIFI_PASSWORD="$WIFI_PASSWORD" ./setup_esp32.sh
```
Expected: script exits with "Flash complete" or similar success message.

**1.2 Verify ESP32 is streaming CSI**
```bash
# Plug ESP32 into USB first
PORT=$(ls /dev/cu.usbserial-* 2>/dev/null | head -1)
timeout 5 cat "$PORT" 2>/dev/null | grep -c "CSI_DATA" || echo "no CSI data yet"
```
Expected: one or more `CSI_DATA` lines within 5 seconds.
If zero: press the EN/RST button on the ESP32, wait 5s, retry.
If still zero: run `WIFI_SSID="$WIFI_SSID" WIFI_PASSWORD="$WIFI_PASSWORD" ./setup_esp32.sh` again.

---

### Phase 2 — Pi system setup (run via SSH)

**2.1 Test SSH connectivity**
```bash
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
    ${PI_USER}@${PI_IP} "echo OK"
```
Expected output: `OK`
If fails: verify Pi is on network, check IP, ensure SSH is enabled in Imager settings.

**2.2 Upload setup script**
```bash
scp "$REPO_DIR/setup_pi.sh" ${PI_USER}@${PI_IP}:~/setup_pi.sh
```

**2.3 Run setup on Pi (fully automated)**
```bash
ssh ${PI_USER}@${PI_IP} "
  export WIVI_REPO='https://github.com/YOUR_USER/wivi-sentinel.git'
  export WIVI_DIR='\$HOME/wivi-sentinel'
  export WIVI_USER='${PI_USER}'
  export CSI_SOURCE=esp32
  export ESP32_SERIAL_PORT=/dev/ttyUSB0
  export ESP32_BAUD_RATE=921600
  export FLASK_PORT=5555
  bash ~/setup_pi.sh
"
```
Expected: script completes with summary banner showing dashboard URL.
Duration: 2–5 minutes (pip install).

**2.4 Verify Python environment**
```bash
ssh ${PI_USER}@${PI_IP} \
  "~/wivi-sentinel/venv/bin/python3 -c 'import flask, numpy, scipy, serial; print(\"deps OK\")'"
```
Expected: `deps OK`

---

### Phase 3 — Connect ESP32 and start

**3.1 Move ESP32 from host to Pi USB**
(This is the one physical action — instruct the user if operating remotely)

**3.2 Verify ESP32 appears on Pi**
```bash
ssh ${PI_USER}@${PI_IP} "ls /dev/ttyUSB*"
```
Expected: `/dev/ttyUSB0`
If missing: check USB cable (must be data cable), try different USB port.

**3.3 Start all services**
```bash
ssh ${PI_USER}@${PI_IP} "~/wivi-sentinel/start.sh"
```
`start.sh` auto-detects the systemd unit. If the unit is installed and already active it
restarts it; otherwise it starts it. Never spawn `python3 server.py` directly on the Pi
if the systemd unit is installed — use `start.sh` or `systemctl` only.

**3.4 Verify services are running**
```bash
ssh ${PI_USER}@${PI_IP} "wivi-status"
```
Expected: `sentinel: RUNNING`.

---

### Phase 4 — Smoke test

**4.1 API health check**
```bash
curl -sf "http://${PI_IP}:5555/api/status" | python3 -m json.tool
```
Expected: JSON with `"status": "running"` and `"csi_source": "esp32"`.

**4.2 Check for CSI data**
```bash
# Wait 15 seconds, then check profiles
sleep 15
curl -sf "http://${PI_IP}:5555/api/profiles" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{len(d['profiles'])} profiles detected\")"
```
Expected: `1 profiles detected` or more within 30 seconds.
If zero after 60s:
- Check ESP32 is on WiFi: `curl http://${PI_IP}:5555/api/esp32/wifi`
- Check serial: `ssh ${PI_USER}@${PI_IP} "timeout 3 cat /dev/ttyUSB0 | head -5"`

**4.3 Open dashboard**
```
http://${PI_IP}:5555
```

---

### Automated re-deploy (subsequent updates)

When a new version is published, pull and restart on the Pi:

```bash
ssh ${PI_USER}@${PI_IP} "cd ~/wivi-sentinel && git pull && ./start.sh restart"
```

---

### Rollback

```bash
# Stop everything
ssh ${PI_USER}@${PI_IP} "~/wivi-sentinel/start.sh stop"

# Clear profiles
ssh ${PI_USER}@${PI_IP} "echo '{}' > ~/wivi-sentinel/data/profiles.json"

# Restart
ssh ${PI_USER}@${PI_IP} "~/wivi-sentinel/start.sh restart"
```

---

## RPM Package (Fedora / RHEL / CentOS)

For RPM-based systems, a self-contained package with an interactive setup wizard is available.

### Install

```bash
# Download the latest RPM from GitHub Releases (or build it yourself)
sudo dnf install ./wivi-sentinel-2.0.0-1.noarch.rpm
```

### Run the setup wizard

```bash
sudo wivi-sentinel-setup
```

The wizard walks you through:
1. **WiFi** — scans available networks, connects via NetworkManager
2. **ESP32** — auto-detects USB serial devices (or starts in demo mode)
3. **Start** — writes config, enables the systemd service, prints the dashboard URL

The RPM handles everything: Python venv, firewall port, service user, serial permissions, and auto-start on boot. Profile data at `/opt/wivi-sentinel/data/` is preserved across upgrades and uninstalls.

### Build the RPM yourself

Requires a Fedora/RHEL machine (or use the GitHub Actions workflow):

```bash
sudo dnf install rpm-build rpmdevtools
./rpm/build-rpm.sh
# Output: wivi-sentinel-2.0.0-1.noarch.rpm
```

Or push a version tag to trigger the CI build:

```bash
git tag v2.0.0
git push origin v2.0.0
# → GitHub Actions builds the RPM and attaches it to the Release
```

---

## Docker Mode (alternative to systemd)

Run the full stack in Docker Compose (ESP32 USB passthrough required):

```bash
# On the Pi:
cd ~/wivi-sentinel

# Build the sentinel image
docker build -t wivi-sentinel .

# Start both services
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

The `docker-compose.yml` passes `/dev/ttyUSB0` into the sentinel container.

> **Note:** If you added your user to the `docker` group during setup, log out and back in before running Docker commands directly. Or prefix with `sudo`.

---

## ESP32 Setup Details

### What `setup_esp32.sh` does

1. Installs ESP-IDF (Espressif IoT Development Framework)
2. Clones `esp-csi` repository (Espressif's CSI tools)
3. Patches `app_main.c` with custom WiFi/NVS/UART command handler
4. Writes WiFi credentials to `sdkconfig.defaults`
5. Handles ESP-IDF version compatibility (copies prebuilt libraries)
6. Builds and flashes `csi_recv_router` firmware

### Changing WiFi credentials after flashing

From the dashboard: open any profile card → click "ESP32 WIFI" in the sidebar → enter new SSID + password → SET WIFI.
The ESP32 saves credentials to NVS (non-volatile storage) and reboots.

Via API:
```bash
curl -X POST http://<pi-ip>:5555/api/esp32/wifi \
  -H "Content-Type: application/json" \
  -d '{"ssid": "NewNetwork", "password": "NewPassword"}'
```

### Verifying CSI output

```bash
source ~/esp/esp-idf/export.sh
idf.py monitor -p /dev/cu.usbserial-*
# Should show CSI_DATA lines after WiFi connects
# Press Ctrl+] to quit
```

---

## Configuration Reference

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `FLASK_PORT` | `5555` | Flask API + dashboard port |
| `VITE_PORT` | `3000` | Vite dev server port (dev mode only) |
| `CSI_SOURCE` | `simulated` | `simulated`, `esp32`, or `nexmon` |
| `ESP32_SERIAL_PORT` | `/dev/ttyUSB0` | ESP32 USB serial device |
| `ESP32_BAUD_RATE` | `921600` | Must match firmware config |
| `PROBE_IFACE` | _(none)_ | Monitor interface for probe sniffing (`mon0`) |

### `start.sh` modes

```bash
./start.sh          # auto: systemctl if unit installed, else direct process
./start.sh restart  # sudo systemctl restart wivi-sentinel
./start.sh stop     # sudo systemctl stop wivi-sentinel
./start.sh status   # systemctl status wivi-sentinel
./start.sh logs     # journalctl -u wivi-sentinel -f
./start.sh dev      # Flask API + Vite hot-reload (Mac dev, requires Node >=18)
./start.sh build    # build dist/ only
```

> On the Pi, `auto` mode auto-detects the installed systemd unit and delegates to
> `systemctl`. If the unit is not installed it falls back to spawning `python3 server.py`
> directly (useful during first-time setup or on machines without systemd).

---

## API Reference

Base URL: `http://<pi-ip>:5555`

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | System status, active signals, config |
| GET | `/api/profiles` | All biometric profiles (coalesced) |
| POST | `/api/profiles/tag` | `{profile_id, nickname}` — tag a profile |
| DELETE | `/api/profiles/:id` | Scrub profile (wipes signature data) |
| GET | `/api/detections` | Current detections + recent history |
| GET | `/api/devices` | Visible nearby devices + correlation scores |
| POST | `/api/devices/suggest` | `{profile_id, device_name}` — suggest association |
| POST | `/api/esp32/wifi` | `{ssid, password}` — update ESP32 WiFi (reboots) |
| GET | `/api/esp32/wifi` | Current ESP32 WiFi connection status |
| GET | `/api/stream` | Server-sent events (real-time detection stream) |

---

## Project Structure

```
wivi-sentinel/
├── server.py               # Flask API server + detection loop
├── start.sh                # Unified start script (dev/prod/systemd)
├── setup_pi.sh             # Automated Pi deploy + systemd
├── setup_esp32.sh          # ESP32 firmware build + flash (run on Mac/Linux, not Pi)
├── Dockerfile              # Wi-Vi Sentinel container image
├── docker-compose.yml      # Docker: sentinel container with ESP32 passthrough
├── engine/
│   ├── csi_processor.py    # Signal processing, classifiers, ProfileStore, coalescing
│   ├── esp32_source.py     # ESP32 CSI source (USB serial + WiFi config commands)
│   ├── nexmon_source.py    # Nexmon CSI source (UDP, legacy)
│   ├── device_scanner.py   # mDNS + probe request device discovery
│   └── csi_collector.py    # CSI frame collection utilities
├── rpm/
│   ├── wivi-sentinel.spec  # RPM package spec
│   ├── wivi-sentinel-setup # Interactive WiFi + ESP32 setup wizard
│   ├── wivi-sentinel.service # Hardened systemd unit for RPM installs
│   └── build-rpm.sh        # Builds the .noarch.rpm
├── .github/workflows/
│   └── build-rpm.yml       # CI: builds RPM on tag push or manual trigger
├── firmware/
│   └── csi_recv_router/    # Patched ESP32 firmware (NVS WiFi + UART commands)
├── src/                    # Vite/React dashboard source
│   ├── App.jsx             # Dashboard UI (cards, compact, radar)
│   ├── main.jsx
│   └── index.css
├── dist/                   # Pre-built dashboard (committed — no build step needed on Pi)
├── index.legacy.html       # CDN Babel fallback (no build required)
├── vite.config.js
├── package.json
├── requirements.txt        # Python deps
├── .env.example            # Config template (copy to .env)
└── data/
    └── profiles.json       # Biometric profiles (gitignored — each install tracks its own)
```

---

## Troubleshooting

### ESP32 not sending CSI

```bash
# Check serial device exists
ls /dev/ttyUSB*          # Pi / Linux
ls /dev/cu.usbserial-*   # macOS

# Check ESP32 is on WiFi
curl http://<pi-ip>:5555/api/esp32/wifi

# Watch raw serial output
cat /dev/ttyUSB0 | head -20
# Should see CSI_DATA lines. If blank: press EN/RST button on ESP32.

# Generate WiFi traffic to trigger more CSI frames
ping <router-ip>
```

Common causes: charge-only USB cable, 5 GHz-only SSID, wrong baud rate, wrong serial port.

### No profiles appearing

```bash
# Check detection loop is running
curl http://<pi-ip>:5555/api/status

# Check signal quality — look for non-zero variance in server logs
sudo journalctl -u wivi-sentinel -f
# Should see [DBG] var=... lines every ~1 second

# Low signal: move closer, reduce obstructions, generate WiFi traffic
ping <router-ip>
```

### Dashboard shows "OFFLINE"

Flask is not running or unreachable. Check:
```bash
sudo systemctl status wivi-sentinel
sudo journalctl -u wivi-sentinel -n 50
```

### Permission denied on /dev/ttyUSB0

```bash
sudo usermod -aG dialout $USER
# Log out and back in, then retry
```

### Build error: esp_csi_gain_ctrl missing version

```
No rule to make target .../6.1/esp32/libesp_csi_gain_ctrl.a
```
```bash
cd managed_components/espressif__esp_csi_gain_ctrl
cp -r 6.0 6.1   # adjust versions as needed
```

---

## Nexmon Legacy

The original architecture used the Pi 4's onboard BCM43455c0 WiFi chip with Nexmon CSI firmware in monitor mode. This is superseded by the ESP32 approach and is only relevant if you have specific hardware constraints.

Set `CSI_SOURCE=nexmon` in `.env` and refer to the Nexmon documentation if needed. The `csi_extractor.py` and legacy `setup_pi.sh` Nexmon sections apply to that path.
