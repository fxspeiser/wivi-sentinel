# Wi-Vi Sentinel — Raspberry Pi 4 Model B Setup

## Hardware Required

- **Raspberry Pi 4 Model B** (any RAM variant)
- **Micro SD card** (16GB+ recommended)
- **USB-C power supply** (official Pi 4 PSU recommended)
- **Ethernet cable** (required — onboard WiFi goes to monitor mode)
- **Your existing home router** (provides the WiFi signal to sense)

## Quick Start

### 1. Flash Raspberry Pi OS

Download **Raspberry Pi OS Lite (32-bit, Bullseye)** from:
https://www.raspberrypi.com/software/

32-bit Bullseye is recommended — Nexmon is most tested against this.

Flash it with Raspberry Pi Imager. In the imager settings:
- Enable SSH
- Set username/password
- Configure WiFi (temporary — for initial setup only)

### 2. Boot and Connect

Plug in Ethernet, boot the Pi, SSH in:

```bash
ssh pi@raspberrypi.local
# or find the IP on your router and: ssh pi@<ip>
```

### 3. Copy Files to the Pi

From your Mac, in the `wivi-sentinel-pi/` directory:

```bash
# Create temp directory on Pi
ssh pi@raspberrypi.local "mkdir -p /tmp/wivi_pi_files"

# Copy files
scp setup_pi.sh pi@raspberrypi.local:/tmp/wivi_pi_files/
scp csi_extractor.py pi@raspberrypi.local:/tmp/wivi_pi_files/
```

### 4. Run Setup

On the Pi:

```bash
cd /tmp/wivi_pi_files
chmod +x setup_pi.sh
sudo ./setup_pi.sh
```

This takes 15-30 minutes. It:
- Installs build tools
- Clones and builds Nexmon
- Patches the BCM43455c0 firmware for CSI extraction
- Installs the extractor daemon
- Creates systemd service and convenience scripts

### 5. Configure

Edit the config on the Pi:

```bash
sudo nano /opt/wivi-sentinel/config.json
```

Set these values:

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

**mac_ip**: Your Mac's IP address on the local network.
Find it with: `ifconfig en0` on your Mac (look for `inet` address).
Or set to `"auto"` and the Pi will scan for the dashboard.

**monitor_channel**: The WiFi channel your router uses.
Find it on your Mac: hold Option, click the WiFi icon in the menu bar.
Look for "Channel" — use the primary channel number (e.g., 36, 44, 149).

**bandwidth**: Match your router. Most 5GHz routers use 80MHz.
If unsure, start with 20 (most compatible) and increase.

### 6. Copy the Extractor Script

```bash
sudo cp /tmp/wivi_pi_files/csi_extractor.py /opt/wivi-sentinel/
```

### 7. Start on the Pi

**Make sure Ethernet is connected first!**
Monitor mode takes over the WiFi chip — you lose WiFi connectivity.

```bash
wivi-start
```

Check it's running:

```bash
wivi-status
```

Watch live logs:

```bash
sudo journalctl -u wivi-csi -f
```

### 8. Start the Dashboard on Your Mac

In your `wivi-sentinel/` directory on your Mac:

```bash
# Switch to live mode
CSI_SOURCE=nexmon python3 server.py
```

Open **http://localhost:5555**

The dashboard will show "nexmon" as the CSI source. Once the Pi starts
sending packets, you'll see real detections appear.

## Stopping

On the Pi:
```bash
wivi-stop
```
This restores normal WiFi so the Pi can reconnect wirelessly.

On your Mac:
```bash
Ctrl+C  # in the server terminal
```

## Running Simulated Mode (No Pi)

Just start normally without the env var:

```bash
python3 server.py
```

## Troubleshooting

### Pi not sending data

Check the service:
```bash
sudo journalctl -u wivi-csi -n 50
```

Verify monitor mode:
```bash
iw dev wlan0 info
# Should show "type monitor"
```

### Mac not receiving

Check UDP port is open:
```bash
# On Mac, verify nothing else is using port 5500:
lsof -i :5500
```

Check packets are arriving:
```bash
# On Mac:
sudo tcpdump -i en0 udp port 5500 -c 5
```

### Wrong channel

If you see packets but no useful CSI, the channel might not match
your router. On the Pi:

```bash
# Check what channels have traffic:
sudo iw dev wlan0 scan | grep -E "freq|signal"
```

### Firmware issues after kernel update

Raspberry Pi OS updates can replace the patched firmware:

```bash
# Reinstall patched firmware:
cd /opt/nexmon_csi/patches/bcm43455c0/7_45_189/nexmon_csi/
make install
```

## Network Diagram

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
       │ UDP :5500 (CSI frames)
       │ over Ethernet
       │
  [Your Mac]
       │  server.py (NexmonCSISource)
       │  Signal processing engine
       │  Species / sex / direction classifiers
       │
       └──► http://localhost:5555
             Wi-Vi Sentinel Dashboard
```