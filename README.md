# Wi-Vi Sentinel

WiFi CSI biometric detection, classification, and tracking.
Passive through-wall sensing using an ESP32 + Raspberry Pi 4 + optional RuView pose estimation.

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
       │  Optional: RuView (Docker) — 17-keypoint pose estimation
       │
       └──► http://<pi-ip>:5555  (dashboard + API)
            http://<pi-ip>:3100  (RuView Observatory)
```

---

## Hardware Required

| Item | Notes |
|---|---|
| **ESP32-DevKitC-32E** | CSI capture (~$10). Must be the 32E variant — S3/C6 don't work with this firmware |
| **Raspberry Pi 4 Model B** | Any RAM size. Runs server, Docker, RuView |
| **USB-A to Micro-USB cable** | Data cable (not charge-only) — connects ESP32 to Pi |
| **Micro SD card** (16 GB+) | For Pi OS |
| **USB-C power supply** | For Pi (≥3A recommended) |
| **Ethernet cable** | Strongly recommended — Pi WiFi is free but router DHCP can be unreliable |

> The Pi's WiFi card is unused — the ESP32 handles CSI capture. Connect the Pi via Ethernet for a stable server connection.

---

## Quick Start

### Step 1 — Flash the ESP32 (on your Mac or Linux machine, not the Pi)

```bash
chmod +x setup_esp32.sh

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

Boot the Pi, confirm you can SSH in:
```bash
ssh doc@192.168.1.148   # use your Pi's IP
```

### Step 3 — Run the setup script on the Pi

```bash
# SSH into the Pi, then:
curl -fsSL https://raw.githubusercontent.com/YOUR_USER/wivi-sentinel/main/setup_pi.sh | bash

# Or with pre-set values (no prompts):
WIVI_REPO="https://github.com/YOUR_USER/wivi-sentinel.git" \
CSI_SOURCE=esp32 \
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USER/wivi-sentinel/main/setup_pi.sh)
```

### Step 4 — Build and deploy the dashboard (on your Mac)

The Pi has Node v12 which is too old for Vite. Build on your Mac, deploy to Pi:

```bash
npm install
npm run build
rsync -av dist/ doc@192.168.1.148:~/wivi-sentinel/dist/
```

### Step 5 — Plug in the ESP32 and start

```bash
# On the Pi:
wivi-start
```

Open `http://192.168.1.148:5555` in your browser.

---

## 🤖 AI Agent Setup Guide

> **For Claude, Codex, Cursor, and other AI coding assistants.**
> This section is written as executable instructions. Follow each block in sequence.
> Verify each step before proceeding. Zero human interaction required after you have:
> - Pi IP address
> - Pi SSH username + password
> - WiFi SSID + password (for ESP32)
> - Path to this repository on the Mac/host machine

---

### Context you need before starting

```
PI_IP=<from user>             # e.g. 192.168.1.148
PI_USER=<from user>           # e.g. doc
PI_PASS=<from user>           # for sudo operations
REPO_DIR=<path on Mac>        # e.g. /Users/fspeiser/Documents/GitHub/wivi-sentinel
WIFI_SSID=<from user>         # ESP32 must join a 2.4 GHz network
WIFI_PASSWORD=<from user>
```

---

### Phase 1 — Flash the ESP32 (run on Mac/host)

**1.1 Flash firmware**
```bash
cd "$REPO_DIR"
WIFI_SSID="$WIFI_SSID" WIFI_PASSWORD="$WIFI_PASSWORD" ./setup_esp32.sh
```
Expected: script exits with "Flash complete" or similar success message.

**1.2 Verify ESP32 is streaming CSI**
```bash
# Plug ESP32 into Mac USB first
PORT=$(ls /dev/cu.usbserial-* 2>/dev/null | head -1)
timeout 5 cat "$PORT" 2>/dev/null | grep -c "CSI_DATA" || echo "no CSI data yet"
```
Expected: one or more `CSI_DATA` lines within 5 seconds.
If zero: press the EN/RST button on the ESP32, wait 5s, retry.
If still zero: run `WIFI_SSID="$WIFI_SSID" WIFI_PASSWORD="$WIFI_PASSWORD" ./setup_esp32.sh` again.

---

### Phase 2 — Pi system setup (run on Mac via SSH)

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
  export RUVIEW_PORT=3100
  export RUVIEW_ENABLED=true
  bash ~/setup_pi.sh
"
```
Expected: script completes with summary banner showing dashboard URL.
Duration: 3–8 minutes (Docker install + pip install).

**2.4 Verify Python environment**
```bash
ssh ${PI_USER}@${PI_IP} \
  "~/wivi-sentinel/venv/bin/python3 -c 'import flask, numpy, scipy, serial; print(\"deps OK\")'"
```
Expected: `deps OK`

**2.5 Verify Docker**
```bash
ssh ${PI_USER}@${PI_IP} "docker --version && docker ps"
```
Expected: Docker version line + running containers list (may include `wivi-ruview`).

---

### Phase 3 — Build and deploy dashboard (run on Mac)

**3.1 Build Vite dashboard**
```bash
cd "$REPO_DIR"
npm install --silent
npm run build
```
Expected: `dist/index.html` created (~200 KB JS bundle).

**3.2 Deploy to Pi**
```bash
rsync -av --delete \
  "$REPO_DIR/dist/" \
  ${PI_USER}@${PI_IP}:~/wivi-sentinel/dist/
```
Expected: file list ending with `sent ... bytes`.

**3.3 Verify dashboard file on Pi**
```bash
ssh ${PI_USER}@${PI_IP} "ls -lh ~/wivi-sentinel/dist/index.html"
```
Expected: file exists, size ~500 bytes.

---

### Phase 4 — Connect ESP32 and start

**4.1 Move ESP32 from Mac to Pi USB**
(This is the one physical action — instruct the user if operating remotely)

**4.2 Verify ESP32 appears on Pi**
```bash
ssh ${PI_USER}@${PI_IP} "ls /dev/ttyUSB*"
```
Expected: `/dev/ttyUSB0`
If missing: check USB cable (must be data cable), try different USB port.

**4.3 Start all services**
```bash
ssh ${PI_USER}@${PI_IP} "wivi-start"
```
Or via systemd:
```bash
ssh ${PI_USER}@${PI_IP} "sudo systemctl start wivi-ruview wivi-sentinel"
```

**4.4 Verify services are running**
```bash
ssh ${PI_USER}@${PI_IP} "wivi-status"
```
Expected: both `sentinel: RUNNING` and `ruview: RUNNING`.

---

### Phase 5 — Smoke test

**5.1 API health check**
```bash
curl -sf "http://${PI_IP}:5555/api/status" | python3 -m json.tool
```
Expected: JSON with `"status": "running"` and `"csi_source": "esp32"`.

**5.2 Check for CSI data**
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

**5.3 RuView health check**
```bash
curl -sf "http://${PI_IP}:3100/health" || echo "RuView not yet ready (may still be starting)"
```

**5.4 Open dashboard**
```
http://${PI_IP}:5555
```

---

### Automated re-deploy (subsequent updates)

When code changes on the Mac, run this to update the Pi and rebuild:

```bash
# From Mac, in repo directory:
npm run build && \
rsync -av --exclude node_modules --exclude .git --exclude venv --exclude data \
  ./ ${PI_USER}@${PI_IP}:~/wivi-sentinel/ && \
ssh ${PI_USER}@${PI_IP} "sudo systemctl restart wivi-sentinel"
```

---

### Rollback

```bash
# Stop everything
ssh ${PI_USER}@${PI_IP} "wivi-stop"

# Clear profiles
ssh ${PI_USER}@${PI_IP} "echo '{}' > ~/wivi-sentinel/data/profiles.json"

# Restart
ssh ${PI_USER}@${PI_IP} "wivi-start"
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

The `docker-compose.yml` passes `/dev/ttyUSB0` into the sentinel container and connects it to the RuView container over the internal Docker network.

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
| `RUVIEW_PORT` | `3100` | Host port for RuView Docker container |
| `RUVIEW_URL` | `http://localhost:3100` | URL Flask proxies RuView requests to |
| `RUVIEW_ENABLED` | `true` | Set `false` to skip Docker/RuView entirely |
| `RUVIEW_CSI_SOURCE` | `simulated` | RuView's own CSI source (usually `simulated`) |

### `start.sh` modes

```bash
./start.sh          # auto: prod if Node <18, dev if Node >=18
./start.sh prod     # Flask serves pre-built dist/ (Pi)
./start.sh dev      # Flask API + Vite hot-reload (Mac dev)
./start.sh build    # build dist/ only (Mac)
```

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
| GET | `/api/ruview/status` | RuView reachability check |
| GET | `/api/ruview/pose` | Proxied: RuView 17-keypoint pose |
| GET | `/api/ruview/vitals` | Proxied: RuView breathing + heart rate |
| GET | `/api/stream` | Server-sent events (real-time detection stream) |

---

## Project Structure

```
wivi-sentinel/
├── server.py               # Flask API server + detection loop
├── start.sh                # Unified start script (dev/prod/build)
├── setup_pi.sh             # Automated Pi deploy + Docker + systemd
├── setup_esp32.sh          # ESP32 firmware build + flash
├── Dockerfile              # Wi-Vi Sentinel container image
├── docker-compose.yml      # Full stack: sentinel + ruview
├── engine/
│   ├── csi_processor.py    # Signal processing, classifiers, ProfileStore, coalescing
│   ├── esp32_source.py     # ESP32 CSI source (USB serial + WiFi config commands)
│   ├── nexmon_source.py    # Nexmon CSI source (UDP, legacy)
│   ├── device_scanner.py   # mDNS + probe request device discovery
│   └── csi_collector.py    # CSI frame collection utilities
├── firmware/
│   └── csi_recv_router/    # Patched ESP32 firmware (NVS WiFi + UART commands)
├── src/                    # Vite/React dashboard source
│   ├── App.jsx             # Dashboard UI (cards, compact, radar, RuView modal)
│   ├── main.jsx
│   └── index.css
├── dist/                   # Vite build output (gitignored — build on Mac, rsync to Pi)
├── index.legacy.html       # CDN Babel fallback (no build required)
├── vite.config.js
├── package.json
├── requirements.txt        # Python deps
├── .env.example            # Config template (copy to .env)
└── data/
    └── profiles.json       # Biometric profiles (gitignored)
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

### RuView not reachable

```bash
# Check container status
docker ps | grep ruview
docker logs wivi-ruview

# Restart container
docker restart wivi-ruview

# Check port is exposed
curl http://localhost:3100/health
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

### Docker permission denied

```bash
sudo usermod -aG docker $USER
# Log out and back in, then retry
newgrp docker   # or: sg docker -c "wivi-start"
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
