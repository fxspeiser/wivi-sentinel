# Wi-Vi Sentinel

WiFi CSI biometric detection, classification, and tracking system.
Passive through-wall sensing using an ESP32 + Raspberry Pi 4.

## How It Works

```
                         WiFi signals (2.4 GHz)
  [Your Router] ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
       │                                      │
       │                            (bodies disturb signal)
       │                                      │
       │         [ESP32-DevKitC-32E] ◄─ ─ ─ ─ ┘
       │              │  Captures CSI (Channel State Information)
       │              │  from every WiFi packet on the network
       │              │
       │              │ USB serial @ 921600 baud
       │              │
  [Raspberry Pi 4] ◄──┘
       │  server.py — signal processing, classification
       │  Species / sex / direction / device correlation
       │
       └──► http://<pi-ip>:5555
             Wi-Vi Sentinel Dashboard
```

The ESP32 connects to your 2.4 GHz WiFi network and extracts CSI
(Channel State Information) from every packet. CSI captures how radio
signals are distorted by nearby bodies — breathing modulates the signal
at ~0.2 Hz, heartbeats at ~1 Hz, and walking creates distinctive gait
patterns.

The Pi reads CSI frames over USB serial, classifies detected biometric
signatures (species, sex, direction of movement), and serves a real-time
dashboard.

---

## Hardware Required

- **ESP32-DevKitC-32E** — CSI capture device (~$10)
- **Raspberry Pi 4 Model B** (any RAM variant) — runs the server
- **USB-A to Micro-USB data cable** — connects ESP32 to Pi (must be a data cable, not charge-only)
- **Micro SD card** (16 GB+) for the Pi
- **USB-C power supply** for the Pi
- **Ethernet cable** (recommended) — for reliable Pi network connectivity
- **Your existing router** — must broadcast on **2.4 GHz** (ESP32 is 2.4 GHz only)

> **Note:** The Pi's WiFi is free since the ESP32 handles CSI capture over USB.
> However, some routers (e.g. Spectrum) may not issue DHCP leases over WiFi
> reliably. If the Pi can't get a WiFi IP, use **Ethernet** for connectivity.
> An Ethernet cable is recommended for the most reliable setup.

---

## Quick Start

### 1. Flash the ESP32

On your Mac or Linux machine (not the Pi):

```bash
chmod +x setup_esp32.sh
./setup_esp32.sh
```

This script automates the full ESP32 setup:
- Installs ESP-IDF (Espressif IoT Development Framework)
- Clones the esp-csi repository
- Prompts for your 2.4 GHz WiFi credentials
- Builds and flashes the `csi_recv_router` firmware

You can also set environment variables to skip the prompts:

```bash
WIFI_SSID="YourNetwork" WIFI_PASSWORD="YourPassword" ./setup_esp32.sh
```

See [ESP32 Setup Details](#esp32-setup-details) for manual steps and troubleshooting.

### 2. Set up the Raspberry Pi

Flash **Raspberry Pi OS Lite** with Raspberry Pi Imager. In settings:
- Enable SSH
- Set username/password
- Configure WiFi

Boot the Pi, SSH in, and install dependencies:

```bash
ssh pi@raspberrypi.local
sudo apt update && sudo apt install python3 python3-pip python3-venv
```

### 3. Deploy Wi-Vi Sentinel to the Pi

Clone the repo on the Pi (or copy from your Mac):

```bash
# Option A: clone directly on the Pi
ssh pi@raspberrypi.local
git clone https://github.com/YOUR_USER/wivi-sentinel.git ~/wivi-sentinel

# Option B: copy from your Mac
scp -r . pi@raspberrypi.local:~/wivi-sentinel/
```

On the Pi, install Python dependencies:

```bash
cd ~/wivi-sentinel
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Build and deploy the dashboard (on your Mac, since the Pi may not have Node):

```bash
npm install && npm run build
scp -r dist pi@raspberrypi.local:~/wivi-sentinel/
```

> **Note:** `dist/` is gitignored, so after `git pull` on the Pi you'll need to
> re-scp the `dist/` folder or build it locally with `npm run build`.

### 4. Connect the ESP32

1. Unplug the ESP32 from your Mac
2. Plug it into the Pi's USB port
3. Verify the serial device appears:

```bash
ls /dev/ttyUSB*   # should show /dev/ttyUSB0
```

### 5. Configure and run

```bash
cp .env.example .env
nano .env
```

Set:
```dotenv
CSI_SOURCE=esp32
ESP32_SERIAL_PORT=/dev/ttyUSB0
ESP32_BAUD_RATE=921600
```

Start the server:
```bash
python3 server.py
```

Open `http://<pi-ip>:5555` in your browser.

---

## Host Machine Setup (Mac / Linux)

You can also run `server.py` on your Mac/Linux machine instead of the Pi
(useful for development or when the ESP32 is plugged directly into your machine).

### Prerequisites

**macOS**

```bash
brew install python node
```

**Linux (Debian / Ubuntu)**

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv nodejs npm

# mDNS device scanning requires avahi-daemon:
sudo apt install avahi-daemon
sudo systemctl enable --now avahi-daemon
```

> **Probe request sniffing on Linux** — `scapy` needs raw socket access. Either run `server.py` with `sudo`, or grant the capability without root:
> ```bash
> sudo setcap cap_net_raw+ep $(which python3)
> ```
> mDNS scanning works without elevated privileges.

---

### Python backend

```bash
pip3 install -r requirements.txt
# or inside a virtualenv:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Dashboard (two options)

**Option A — no build step (CDN Babel, works immediately):**

```bash
python3 server.py
# Mac:   open http://localhost:5555
# Linux: xdg-open http://localhost:5555
```

**Option B — Vite build (faster, production-ready):**

```bash
npm install
npm run build          # outputs to dist/
python3 server.py      # automatically serves dist/ when present
```

**Option B dev mode (hot reload):**

```bash
npm install
python3 server.py &    # Flask API on :5555
npm run dev            # Vite dev server on :3000, proxies /api to Flask
```

### Simulated mode (no hardware required)

```bash
python3 server.py
```

Generates 5 synthetic entities (3 humans, 1 dog, 1 cat) with realistic biometrics.

---

## ESP32 Setup Details

### What the setup script does

`setup_esp32.sh` performs these steps:

1. **Installs ESP-IDF** — Espressif's IoT Development Framework (release/v5.4)
2. **Clones esp-csi** — Espressif's CSI tools and examples
3. **Configures firmware** — writes WiFi credentials and CSI settings to `sdkconfig.defaults.user`
4. **Handles version compatibility** — copies prebuilt libraries for newer ESP-IDF versions
5. **Builds and flashes** — compiles `csi_recv_router` firmware and flashes it to the ESP32

### Manual ESP32 setup

If you prefer to set up manually or the script doesn't work:

```bash
# 1. Install ESP-IDF
mkdir -p ~/esp
git clone --recursive --branch release/v5.4 \
    https://github.com/espressif/esp-idf.git ~/esp/esp-idf
cd ~/esp/esp-idf && ./install.sh esp32
source ~/esp/esp-idf/export.sh

# 2. Clone esp-csi
git clone https://github.com/espressif/esp-csi.git ~/esp/esp-csi

# 3. Build and flash
cd ~/esp/esp-csi/examples/get-started/csi_recv_router
idf.py set-target esp32
idf.py menuconfig   # Set WiFi SSID/password under "Example Configuration"
idf.py build
idf.py flash -p /dev/cu.usbserial-110   # your port may differ
```

### Verifying CSI output

```bash
source ~/esp/esp-idf/export.sh
cd ~/esp/esp-csi/examples/get-started/csi_recv_router
idf.py monitor -p /dev/cu.usbserial-110
```

You should see WiFi connection logs followed by `CSI_DATA` lines. Press the
**EN/RST** button on the ESP32 if no output appears. Quit with `Ctrl+]`.

### Generating WiFi traffic

The ESP32 captures CSI from WiFi packets on the network. More traffic means
more CSI frames. To generate traffic, ping the router from the Pi:

```bash
ping 192.168.1.1
```

---

## Nexmon CSI (Legacy)

The original architecture used the Pi 4's onboard BCM43455c0 WiFi chip with
Nexmon CSI firmware in monitor mode. This required Ethernet for connectivity
(since WiFi was in monitor mode) and a specific Nexmon firmware patch.

If you prefer the Nexmon approach, see `setup_pi.sh` and set `CSI_SOURCE=nexmon`
in your `.env`. Note that Nexmon firmware compatibility varies by kernel version
and may require troubleshooting.

---

## Device Name Correlation

The system passively discovers nearby device names (phones, laptops) via:
- **mDNS/Bonjour** — picks up hostnames like "Knibb High Football Rules.local"
- **WiFi probe requests** — captured on the monitor interface (set `PROBE_IFACE=mon0`)

As subjects are detected, the system builds a co-presence matrix between
CSI profiles and nearby devices. To associate a profile with a device:

1. Open a profile card in the dashboard
2. Under **DEVICE LINK**, click **TAG** next to a device name
3. The system watches co-presence confidence. Once it exceeds 82% across
   at least 10 observations, it auto-confirms the association and tags the profile.

Or via the API directly:

```bash
curl -X POST http://localhost:5555/api/devices/suggest \
  -H "Content-Type: application/json" \
  -d '{"profile_id": "abc123def456", "device_name": "Knibb High Football Rules"}'
```

Set `PROBE_IFACE=mon0` (or `wlan0`) on the Pi to enable probe request capture.
mDNS scanning runs automatically when `zeroconf` is installed.

---

## Configuration (.env)

All ports and runtime options are configured via a `.env` file in the project root.
Copy the committed example and edit as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `FLASK_PORT` | `5555` | Port the Flask API server listens on |
| `VITE_PORT` | `3000` | Port the Vite dev server listens on |
| `CSI_SOURCE` | `simulated` | `simulated`, `esp32`, or `nexmon` |
| `ESP32_SERIAL_PORT` | `/dev/ttyUSB0` | Serial port for ESP32 USB connection |
| `ESP32_BAUD_RATE` | `921600` | Baud rate (must match firmware config) |
| `CSI_UDP_PORT` | `5500` | UDP port for Nexmon CSI frames (legacy) |
| `PROBE_IFACE` | _(none)_ | Monitor interface for probe sniffing (e.g. `mon0`) |

`.env` is gitignored. `.env.example` is committed as a reference template.

### PROBE_IFACE examples

```dotenv
# Disabled — mDNS still runs, probe sniffing off (default)
PROBE_IFACE=

# Nexmon monitor interface created by setup_pi.sh (legacy setup)
PROBE_IFACE=mon0

# Some setups name it differently — check with: iw dev
PROBE_IFACE=wlan0mon
```

---

## Project Structure

```
wivi-sentinel/
├── server.py               # Flask API server + detection loop
├── engine/
│   ├── __init__.py
│   ├── csi_processor.py    # Signal processing, classifiers, ProfileStore
│   ├── csi_collector.py    # CSI frame collection utilities
│   ├── esp32_source.py     # ESP32 CSI source (USB serial)
│   ├── nexmon_source.py    # Nexmon CSI source (UDP, legacy)
│   └── device_scanner.py   # mDNS + probe request device discovery
├── nexmon_source.py        # Nexmon source (root-level, legacy)
├── csi_extractor.py        # Pi-side CSI capture for Nexmon (legacy)
├── setup_esp32.sh          # ESP32 firmware setup script
├── setup_pi.sh             # Pi setup script (Nexmon, legacy)
├── src/                    # Vite/React dashboard source
│   ├── main.jsx
│   ├── App.jsx             # Dashboard UI (cards, compact view, radar)
│   └── index.css
├── dist/                   # Vite build output (gitignored, scp to Pi)
├── index.html              # Vite entry point
├── index.legacy.html       # CDN Babel fallback (no build required)
├── vite.config.js
├── package.json
├── requirements.txt        # Python deps (flask, numpy, scipy, pyserial, etc.)
├── .env.example            # Reference config (copy to .env locally)
└── data/
    └── profiles.json       # Biometric profiles (gitignored)
```

---

## Troubleshooting

### ESP32

**No serial device found**
```bash
ls /dev/ttyUSB*          # Linux / Pi
ls /dev/cu.usbserial-*   # macOS
```
If nothing appears, try a different USB cable (some are charge-only) or port.

**ESP32 not sending CSI_DATA**
- Verify the ESP32 is connected to your WiFi: look for `sta ip:` in the boot log
- Make sure your router broadcasts on **2.4 GHz** (ESP32 cannot connect to 5 GHz)
- Press the **EN/RST** button on the ESP32 to reboot it
- Generate WiFi traffic: `ping <router-ip>` from any device on the network

**Garbage characters in serial monitor**
- Baud rate mismatch. The firmware is configured for 921600 baud. Use:
  ```bash
  idf.py monitor -p /dev/cu.usbserial-110
  ```
  (reads baud rate from sdkconfig automatically)

**Build error: esp_csi_gain_ctrl missing version**
```
No rule to make target .../6.1/esp32/libesp_csi_gain_ctrl.a
```
The prebuilt library doesn't have your ESP-IDF version yet. Copy the closest:
```bash
cd managed_components/espressif__esp_csi_gain_ctrl
cp -r 6.0 6.1   # adjust versions as needed
```

**`invalid header: 0xffffffff` in boot log**
- The ESP32 flash is empty or corrupted. Reflash:
  ```bash
  idf.py flash -p /dev/cu.usbserial-110
  ```

### Nexmon (legacy)

**Pi not sending data**
```bash
sudo journalctl -u wivi-csi -n 50
iw dev wlan0 info   # should show "type monitor"
```

**Mac not receiving**
```bash
lsof -i :5500                          # check port is free
sudo tcpdump -i en0 udp port 5500 -c 5 # verify packets arriving
```

**Wrong channel / no CSI**
```bash
sudo iw dev wlan0 scan | grep -E "freq|signal"
```

**Firmware replaced after kernel update**
```bash
cd /opt/nexmon_csi/patches/bcm43455c0/7_45_189/nexmon_csi/
make install
```
