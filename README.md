# Wi-Vi Sentinel

WiFi CSI biometric detection, classification, and tracking system.
Passive through-wall sensing using a Raspberry Pi 4 + Nexmon CSI firmware.

## How It Works

```
                         WiFi signals
  [Your Router] ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
       │                                      │
       │ Ethernet                    (bodies disturb signal)
       │                                      │
  [Raspberry Pi 4] ◄─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
       │  BCM43455c0 in monitor mode
       │  Extracts CSI from every packet
       │
       │ UDP :5500 (CSI frames) over Ethernet
       │
  [Your Mac / Linux machine]
       │  server.py — signal processing, classification
       │  Species / sex / direction / device correlation
       │
       └──► http://localhost:5555  (or :3000 in dev mode)
             Wi-Vi Sentinel Dashboard
```

---

## Hardware Required

- **Raspberry Pi 4 Model B** (any RAM variant)
- **Micro SD card** (16 GB+)
- **USB-C power supply** (official Pi 4 PSU recommended)
- **Ethernet cable** — required; onboard WiFi goes into monitor mode
- **Your existing router** — provides the WiFi signal to sense

---

## Host Machine Setup (Mac / Linux)

The host machine runs `server.py` — it receives CSI frames from the Pi, classifies them, and serves the dashboard. Any machine on the same LAN works.

### Prerequisites

**macOS**

Python 3 and Node.js are the only requirements. Both are available via [Homebrew](https://brew.sh):

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

### Simulated mode (no Pi required)

```bash
python3 server.py
```

Generates 5 synthetic entities (3 humans, 1 dog, 1 cat) with realistic biometrics.

---

## Pi Setup

### 1. Flash Raspberry Pi OS

Download **Raspberry Pi OS Lite (32-bit, Bullseye)** from the Raspberry Pi website.

Flash with Raspberry Pi Imager. In imager settings:
- Enable SSH
- Set username/password
- Configure WiFi (temporary — for initial setup only)

### 2. Boot and connect

```bash
ssh pi@raspberrypi.local
```

### 3. Copy files to the Pi

```bash
ssh pi@raspberrypi.local "mkdir -p /tmp/wivi_pi_files"
scp setup_pi.sh pi@raspberrypi.local:/tmp/wivi_pi_files/
scp csi_extractor.py pi@raspberrypi.local:/tmp/wivi_pi_files/
```

### 4. Run setup

```bash
cd /tmp/wivi_pi_files
chmod +x setup_pi.sh
sudo ./setup_pi.sh
```

Takes 15–30 minutes. Installs Nexmon, patches BCM43455c0 firmware, creates
systemd service and convenience scripts.

### 5. Configure

```bash
sudo nano /opt/wivi-sentinel/config.json
```

```json
{
    "mac_ip": "192.168.1.71",
    "udp_port": 5500,
    "monitor_channel": 36,
    "bandwidth": 80,
    "interface": "wlan0",
    "sample_rate": 100,
    "auto_discover_mac": true,
    "log_level": "INFO"
}
```

- **mac_ip** — your Mac's local IP. Find it: hold Option + click WiFi icon → TCP/IP.
  Or set `"auto"` for the Pi to discover it.
- **monitor_channel** — your router's primary WiFi channel.
  Find it on Mac: hold Option + click WiFi icon → Channel.
- **bandwidth** — match your router (80 MHz is common for 5 GHz).

### 6. Start on the Pi

**Connect Ethernet first** — monitor mode disables WiFi connectivity.

```bash
wivi-start
```

```bash
wivi-status          # check service and config
wivi-stop            # stop and restore normal WiFi
sudo journalctl -u wivi-csi -f   # live logs
```

### 7. Start the dashboard in live mode

```bash
CSI_SOURCE=nexmon python3 server.py
```

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
| `CSI_SOURCE` | `simulated` | `simulated` or `nexmon` |
| `CSI_UDP_PORT` | `5500` | UDP port for Nexmon CSI frames from the Pi |
| `PROBE_IFACE` | _(none)_ | Monitor interface for probe sniffing (e.g. `mon0`) |

`.env` is gitignored. `.env.example` is committed as a reference template.

### PROBE_IFACE examples

```dotenv
# Disabled — mDNS still runs, probe sniffing off (default)
PROBE_IFACE=

# Nexmon monitor interface created by setup_pi.sh (most common)
PROBE_IFACE=mon0

# If Nexmon puts the interface directly into monitor mode without a separate mon interface
PROBE_IFACE=wlan0

# Some setups name it differently — check with: iw dev
PROBE_IFACE=wlan0mon
```

> **Note:** `PROBE_IFACE` is read on the **Mac** side only. It tells `server.py` which interface name to pass to `DeviceScanner` for sniffing probe requests forwarded over the network. Set it to the monitor interface visible to `server.py`, not the Pi's local name.

---

## Project Structure

```
wivi-sentinel/
├── server.py               # Flask API server
├── engine/
│   ├── csi_processor.py    # Signal processing, classifiers, ProfileStore, DeviceCorrelator
│   └── device_scanner.py   # mDNS + probe request device discovery
├── csi_extractor.py        # Pi-side CSI capture and UDP forwarding
├── nexmon_source.py        # Live Nexmon CSI source (NexmonCSISource)
├── setup_pi.sh             # Pi setup script (Nexmon build + service install)
├── src/                    # Vite/React dashboard source
│   ├── main.jsx
│   ├── App.jsx
│   └── index.css
├── index.html              # Vite entry point
├── index.legacy.html       # CDN Babel fallback (no build required)
├── vite.config.js
├── package.json
├── requirements.txt
├── .env.example            # reference config (commit this; copy to .env locally)
└── data/
    └── profiles.json       # Biometric profiles (gitignored)
```

---

## Troubleshooting

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
