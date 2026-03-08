#!/usr/bin/env python3
"""
Wi-Vi Sentinel — CSI Extractor Daemon (Raspberry Pi 4)
========================================================
Runs on the Pi in monitor mode. Captures raw CSI packets from the
Nexmon-patched BCM43455c0, parses the I/Q data, and forwards compressed
CSI frames to the Mac over UDP.

Packet flow:
  WiFi channel → BCM43455c0 (Nexmon firmware) → /dev/wlan0 (monitor mode)
  → tcpdump/raw socket → this script → UDP to Mac

Protocol: Each UDP datagram sent to the Mac contains:
  Bytes 0-3:    magic (0x57564353 = "WVCS")
  Bytes 4-7:    sequence number (uint32, little-endian)
  Bytes 8-15:   timestamp (float64, little-endian, time.time())
  Bytes 16-17:  n_subcarriers (uint16, little-endian)
  Bytes 18-19:  rssi (int16, little-endian)
  Bytes 20-21:  channel (uint16, little-endian)
  Bytes 22-23:  bandwidth (uint16, little-endian)
  Bytes 24+:    CSI data as float32 pairs [amp0, phase0, amp1, phase1, ...]

Usage:
  sudo python3 csi_extractor.py --config /opt/wivi-sentinel/config.json

Requires: Nexmon CSI firmware installed, interface in monitor mode.
"""

import argparse
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import time
import signal
import threading
from collections import deque

import numpy as np

# ─── Constants ───────────────────────────────────────────────────────────────

WVCS_MAGIC = 0x57564353          # "WVCS" - Wi-Vi CSI Sentinel
NEXMON_MAGIC = b'\x11\x11'
NEXMON_HEADER_LEN = 18
MAX_UDP_PAYLOAD = 1400           # Stay under MTU

# Subcarrier counts per bandwidth
BW_SUBCARRIERS = {20: 64, 40: 128, 80: 256}

# Null subcarrier indices (centered at DC) per bandwidth
NULL_SC = {
    20: set(range(-32, -28)) | {0} | set(range(29, 32)),
    40: set(range(-64, -58)) | {0} | set(range(59, 64)),
    80: set(range(-128, -122)) | {0} | set(range(123, 128)),
}

# Pilot subcarrier indices per bandwidth
PILOT_SC = {
    20: {-21, -7, 7, 21},
    40: {-53, -25, -11, 11, 25, 53},
    80: {-103, -75, -39, -11, 11, 39, 75, 103},
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('csi_extractor')


class NexmonCSIParser:
    """Parses raw Nexmon CSI packets from the BCM43455c0."""

    def __init__(self, bandwidth=80):
        self.bandwidth = bandwidth
        self.n_raw = BW_SUBCARRIERS.get(bandwidth, 256)
        self.nulls = NULL_SC.get(bandwidth, set())
        self.pilots = PILOT_SC.get(bandwidth, set())

        # Build data subcarrier index list
        half = self.n_raw // 2
        all_indices = list(range(-half, half))
        self.data_indices = [
            i for i in all_indices
            if i not in self.nulls and i not in self.pilots
        ]
        self.n_data = len(self.data_indices)
        log.info(f"Parser: {bandwidth}MHz, {self.n_raw} raw → {self.n_data} data subcarriers")

    def parse(self, raw_bytes: bytes) -> dict:
        """
        Parse a Nexmon CSI packet.

        The packet arrives as raw bytes captured from the monitor interface.
        Nexmon encodes CSI into the payload of 802.11 frames.

        Returns dict with: amplitudes, phases, rssi, seq, timestamp
                 or None if packet is invalid
        """
        # Look for Nexmon magic bytes
        magic_pos = raw_bytes.find(NEXMON_MAGIC)
        if magic_pos < 0:
            return None

        data = raw_bytes[magic_pos:]
        if len(data) < NEXMON_HEADER_LEN + 4:
            return None

        # Parse Nexmon header
        try:
            magic = struct.unpack('<H', data[0:2])[0]
            rssi = struct.unpack('<b', data[2:3])[0]
            fc = struct.unpack('<H', data[4:6])[0]
            chanspec = struct.unpack('<H', data[6:8])[0]
            seq = struct.unpack('<H', data[8:10])[0]
            core_spatial = struct.unpack('<H', data[10:12])[0]
        except struct.error:
            return None

        # CSI data starts after header: int16 I/Q pairs
        csi_bytes = data[NEXMON_HEADER_LEN:]
        n_iq_pairs = len(csi_bytes) // 4  # 2 bytes I + 2 bytes Q

        if n_iq_pairs < 10:
            return None

        # Parse I/Q
        try:
            iq_raw = np.frombuffer(csi_bytes[:n_iq_pairs * 4], dtype=np.int16)
            iq = iq_raw.reshape(-1, 2)
        except ValueError:
            return None

        # Convert to complex
        csi_complex = iq[:, 0].astype(np.float64) + 1j * iq[:, 1].astype(np.float64)

        # Extract data subcarriers (skip nulls and pilots)
        # Nexmon orders subcarriers: [DC, 1, 2, ..., N/2-1, -N/2, ..., -1]
        # Reorder to standard: [-N/2, ..., -1, DC, 1, ..., N/2-1]
        n = len(csi_complex)
        if n >= self.n_raw:
            # FFT shift to center DC
            csi_shifted = np.fft.fftshift(csi_complex[:self.n_raw])
        else:
            csi_shifted = np.fft.fftshift(csi_complex)

        amplitudes = np.abs(csi_shifted)
        phases = np.angle(csi_shifted)

        # Extract only data subcarriers
        half = len(csi_shifted) // 2
        valid_indices = []
        for idx in self.data_indices:
            array_idx = idx + half
            if 0 <= array_idx < len(csi_shifted):
                valid_indices.append(array_idx)

        if len(valid_indices) < 10:
            return None

        data_amps = amplitudes[valid_indices]
        data_phases = phases[valid_indices]

        # Normalize amplitudes to dB scale then back to linear (0-1 range)
        amp_db = 20 * np.log10(data_amps + 1e-10)
        amp_min, amp_max = np.min(amp_db), np.max(amp_db)
        if amp_max - amp_min > 0:
            data_amps_norm = (amp_db - amp_min) / (amp_max - amp_min)
        else:
            data_amps_norm = np.zeros_like(data_amps)

        return {
            'amplitudes': data_amps_norm,
            'phases': data_phases,
            'rssi': int(rssi),
            'seq': int(seq),
            'chanspec': int(chanspec),
            'n_subcarriers': len(valid_indices),
            'timestamp': time.time(),
        }


class CSICaptureEngine:
    """
    Captures raw WiFi frames from the monitor interface using a raw socket.
    Falls back to tcpdump subprocess if raw socket fails.
    """

    def __init__(self, interface='wlan0'):
        self.interface = interface
        self.sock = None
        self.tcpdump_proc = None
        self._init_capture()

    def _init_capture(self):
        """Try raw socket first, fall back to tcpdump."""
        try:
            # Raw socket on the monitor interface
            self.sock = socket.socket(
                socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003)
            )
            self.sock.bind((self.interface, 0))
            self.sock.settimeout(1.0)
            log.info(f"Raw socket capture on {self.interface}")
        except (PermissionError, OSError) as e:
            log.warning(f"Raw socket failed ({e}), falling back to tcpdump")
            self.sock = None
            self._start_tcpdump()

    def _start_tcpdump(self):
        """Start tcpdump as a subprocess for packet capture."""
        cmd = [
            'tcpdump', '-i', self.interface,
            '-w', '-',           # Write to stdout
            '--immediate-mode',  # Don't buffer
            '-U',                # Packet-buffered output
            '-s', '2048',        # Snap length
        ]
        self.tcpdump_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        log.info(f"tcpdump capture on {self.interface}")

    def read_packet(self) -> bytes:
        """Read one raw packet. Returns bytes or None."""
        if self.sock:
            try:
                data, _ = self.sock.recvfrom(4096)
                return data
            except socket.timeout:
                return None
            except Exception:
                return None
        elif self.tcpdump_proc:
            # Read pcap packet (simplified — real impl would parse pcap header)
            try:
                # Read raw bytes from tcpdump stdout
                header = self.tcpdump_proc.stdout.read(16)
                if len(header) < 16:
                    return None
                # pcap packet header: ts_sec(4) ts_usec(4) incl_len(4) orig_len(4)
                incl_len = struct.unpack('<I', header[8:12])[0]
                data = self.tcpdump_proc.stdout.read(incl_len)
                return data
            except Exception:
                return None
        return None

    def close(self):
        if self.sock:
            self.sock.close()
        if self.tcpdump_proc:
            self.tcpdump_proc.terminate()


class UDPForwarder:
    """Packs CSI frames and sends them to the Mac via UDP."""

    def __init__(self, target_ip, target_port=5500):
        self.target = (target_ip, target_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0
        self.bytes_sent = 0
        self.packets_sent = 0
        log.info(f"UDP forwarder → {target_ip}:{target_port}")

    def send_frame(self, amplitudes: np.ndarray, phases: np.ndarray,
                   rssi: int = 0, channel: int = 0, bandwidth: int = 80):
        """
        Pack and send one CSI frame.

        Wire format:
          magic(4) + seq(4) + timestamp(8) + n_sc(2) + rssi(2) + chan(2) + bw(2)
          + [amp0(f32), phase0(f32), amp1(f32), phase1(f32), ...]
        """
        n_sc = len(amplitudes)

        # Header: 24 bytes
        header = struct.pack('<IIQHHH H',
            WVCS_MAGIC,
            self.seq & 0xFFFFFFFF,
            int(time.time() * 1_000_000),  # microsecond timestamp
            n_sc,
            rssi & 0xFFFF,
            channel & 0xFFFF,
            bandwidth & 0xFFFF,
        )

        # CSI data: interleaved float32 amp/phase pairs
        csi_data = np.empty(n_sc * 2, dtype=np.float32)
        csi_data[0::2] = amplitudes.astype(np.float32)
        csi_data[1::2] = phases.astype(np.float32)

        payload = header + csi_data.tobytes()

        # Fragment if needed (shouldn't be for typical subcarrier counts)
        if len(payload) > MAX_UDP_PAYLOAD:
            # Send in chunks (add fragment header in production)
            log.warning(f"Payload {len(payload)} > MTU, truncating to {MAX_UDP_PAYLOAD}")
            payload = payload[:MAX_UDP_PAYLOAD]

        try:
            self.sock.sendto(payload, self.target)
            self.seq += 1
            self.packets_sent += 1
            self.bytes_sent += len(payload)
        except OSError as e:
            if self.packets_sent % 1000 == 0:
                log.warning(f"UDP send error: {e}")

    def close(self):
        self.sock.close()


class MacDiscovery:
    """Auto-discover the Mac's IP on the local network."""

    @staticmethod
    def find_mac_ip() -> str:
        """
        Try to find the Mac by scanning the local subnet for the dashboard API.
        Falls back to broadcast if discovery fails.
        """
        import subprocess

        # Get our subnet
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            # Parse gateway IP to determine subnet
            parts = result.stdout.split()
            if 'via' in parts:
                gateway = parts[parts.index('via') + 1]
                subnet_prefix = '.'.join(gateway.split('.')[:3])
            else:
                subnet_prefix = '192.168.1'
        except Exception:
            subnet_prefix = '192.168.1'

        log.info(f"Scanning {subnet_prefix}.0/24 for Wi-Vi Sentinel dashboard...")

        # Quick scan: try common IPs and check for the dashboard API
        for last_octet in list(range(1, 255)):
            ip = f"{subnet_prefix}.{last_octet}"
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.15)
            try:
                result = sock.connect_ex((ip, 5555))
                if result == 0:
                    log.info(f"Found dashboard at {ip}:5555")
                    sock.close()
                    return ip
            except:
                pass
            finally:
                sock.close()

        log.warning("Auto-discovery failed. Set mac_ip in config.json manually.")
        return None


def run_extractor(config: dict):
    """Main extraction loop."""
    interface = config.get('interface', 'wlan0')
    mac_ip = config.get('mac_ip', 'auto')
    udp_port = config.get('udp_port', 5500)
    bandwidth = config.get('bandwidth', 80)
    channel = config.get('monitor_channel', 36)

    # Auto-discover Mac if needed
    if mac_ip == 'auto' or not mac_ip:
        log.info("Auto-discovering Mac IP...")
        mac_ip = MacDiscovery.find_mac_ip()
        if not mac_ip:
            log.error("Could not find Mac. Set mac_ip in config.json and restart.")
            sys.exit(1)

    log.info(f"Configuration:")
    log.info(f"  Interface:  {interface}")
    log.info(f"  Channel:    {channel}")
    log.info(f"  Bandwidth:  {bandwidth}MHz")
    log.info(f"  Target:     {mac_ip}:{udp_port}")

    # Initialize components
    parser = NexmonCSIParser(bandwidth=bandwidth)
    capture = CSICaptureEngine(interface=interface)
    forwarder = UDPForwarder(target_ip=mac_ip, target_port=udp_port)

    # Stats tracking
    packets_captured = 0
    packets_parsed = 0
    start_time = time.time()

    def print_stats():
        elapsed = time.time() - start_time
        rate = packets_parsed / max(elapsed, 1)
        log.info(
            f"Stats: captured={packets_captured} parsed={packets_parsed} "
            f"forwarded={forwarder.packets_sent} rate={rate:.1f}pkt/s "
            f"sent={forwarder.bytes_sent/1024:.0f}KB"
        )

    # Stats printer
    def stats_loop():
        while True:
            time.sleep(10)
            print_stats()

    stats_thread = threading.Thread(target=stats_loop, daemon=True)
    stats_thread.start()

    log.info("CSI extraction running. Ctrl+C to stop.")

    try:
        while True:
            raw = capture.read_packet()
            if raw is None:
                continue

            packets_captured += 1
            result = parser.parse(raw)

            if result is None:
                continue

            packets_parsed += 1

            forwarder.send_frame(
                amplitudes=result['amplitudes'],
                phases=result['phases'],
                rssi=result['rssi'],
                channel=channel,
                bandwidth=bandwidth,
            )

    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        print_stats()
        capture.close()
        forwarder.close()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Wi-Vi Sentinel CSI Extractor')
    parser.add_argument('--config', type=str, default='/opt/wivi-sentinel/config.json',
                        help='Path to config JSON')
    parser.add_argument('--mac-ip', type=str, default=None,
                        help='Override Mac IP address')
    parser.add_argument('--port', type=int, default=None,
                        help='Override UDP port')
    parser.add_argument('--channel', type=int, default=None,
                        help='Override monitor channel')
    parser.add_argument('--bandwidth', type=int, default=None,
                        help='Override bandwidth (20/40/80)')
    parser.add_argument('--interface', type=str, default=None,
                        help='Override WiFi interface')
    args = parser.parse_args()

    # Load config
    config = {}
    if os.path.exists(args.config):
        with open(args.config) as f:
            config = json.load(f)
        log.info(f"Loaded config from {args.config}")
    else:
        log.warning(f"Config not found at {args.config}, using defaults")

    # CLI overrides
    if args.mac_ip:
        config['mac_ip'] = args.mac_ip
    if args.port:
        config['udp_port'] = args.port
    if args.channel:
        config['monitor_channel'] = args.channel
    if args.bandwidth:
        config['bandwidth'] = args.bandwidth
    if args.interface:
        config['interface'] = args.interface

    run_extractor(config)


if __name__ == '__main__':
    main()