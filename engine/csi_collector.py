#!/usr/bin/env python3
"""
Nexmon CSI Collector
=====================
Captures CSI frames from the Nexmon-patched brcmfmac driver and forwards
them as UDP packets to localhost:5500 for the NexmonCSISource to consume.

The Nexmon CSI firmware embeds CSI data into special frames that appear
on the WiFi interface. This script reads them via nexutil and sends
them to the application server.

Usage:
    sudo python3 csi_collector.py [--port 5500] [--channel 44/80]

Must run as root (needs access to nexutil).
"""

import socket
import subprocess
import struct
import time
import argparse
import sys
import os
import signal

def get_csi_via_nexutil(interface='wlan0'):
    """
    Read CSI data from the Nexmon firmware via nexutil ioctl.
    Returns raw CSI bytes or None.
    """
    try:
        result = subprocess.run(
            ['nexutil', f'-I{interface}', '-g500', '-l500', '-r'],
            capture_output=True, timeout=1
        )
        if result.returncode == 0 and len(result.stdout) > 18:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def configure_csi(interface, chanspec):
    """Configure CSI extraction via nexutil."""
    try:
        # Generate CSI params
        params = subprocess.run(
            ['makecsiparams', '-c', chanspec, '-C', '1', '-N', '1'],
            capture_output=True, text=True
        )
        if params.returncode != 0:
            print(f"[ERROR] makecsiparams failed: {params.stderr}")
            return False

        csi_params = params.stdout.strip()

        # Apply configuration
        result = subprocess.run(
            ['nexutil', f'-I{interface}', '-s500', '-b', '-l34', f'-vm{csi_params}'],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"[ERROR] nexutil config failed: {result.stderr}")
            return False

        print(f"[CSI] Configured CSI extraction on {interface} channel {chanspec}")
        return True

    except FileNotFoundError as e:
        print(f"[ERROR] Tool not found: {e}")
        return False


def poll_csi_loop(interface, udp_port, poll_hz=100):
    """
    Continuously poll for CSI data via nexutil and forward via UDP.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = ('127.0.0.1', udp_port)

    poll_interval = 1.0 / poll_hz
    frames_sent = 0
    errors = 0
    last_report = time.time()

    print(f"[CSI] Polling CSI at {poll_hz} Hz, forwarding UDP to localhost:{udp_port}")

    while True:
        try:
            data = get_csi_via_nexutil(interface)
            if data and len(data) > 18:
                sock.sendto(data, dest)
                frames_sent += 1
            else:
                time.sleep(poll_interval)

            # Status report every 10 seconds
            now = time.time()
            if now - last_report >= 10:
                rate = frames_sent / (now - last_report)
                print(f"[CSI] {frames_sent} frames sent ({rate:.1f}/s), {errors} errors")
                frames_sent = 0
                errors = 0
                last_report = now

        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"[CSI] Error: {e}")
            time.sleep(0.1)


def capture_pcap_loop(interface, udp_port):
    """
    Alternative: use tcpdump to capture raw frames and extract CSI.
    This works when the firmware sends CSI as special 802.11 frames.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = ('127.0.0.1', udp_port)

    print(f"[CSI] Capturing raw frames on {interface}, forwarding to localhost:{udp_port}")

    proc = subprocess.Popen(
        ['tcpdump', '-i', interface, '-w', '-', '-U', '-s', '0',
         'dst', 'port', '5500', 'or', 'ether', 'proto', '0x8812'],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    # Read pcap global header (24 bytes)
    proc.stdout.read(24)

    frames_sent = 0
    last_report = time.time()

    while True:
        try:
            # Read pcap packet header (16 bytes)
            pkt_hdr = proc.stdout.read(16)
            if len(pkt_hdr) < 16:
                break

            ts_sec, ts_usec, incl_len, orig_len = struct.unpack('<IIII', pkt_hdr)
            pkt_data = proc.stdout.read(incl_len)

            if len(pkt_data) >= 18:
                sock.sendto(pkt_data, dest)
                frames_sent += 1

            now = time.time()
            if now - last_report >= 10:
                rate = frames_sent / (now - last_report)
                print(f"[CSI] {frames_sent} frames captured ({rate:.1f}/s)")
                frames_sent = 0
                last_report = now

        except Exception as e:
            print(f"[CSI] Capture error: {e}")
            break

    proc.terminate()


def main():
    parser = argparse.ArgumentParser(description='Nexmon CSI Collector')
    parser.add_argument('--port', type=int, default=5500, help='UDP port to forward CSI to')
    parser.add_argument('--channel', type=str, default='44/80', help='Channel/bandwidth (e.g., 44/80)')
    parser.add_argument('--interface', type=str, default='wlan0', help='WiFi interface')
    parser.add_argument('--mode', type=str, default='poll', choices=['poll', 'capture'],
                        help='Collection mode: poll (nexutil) or capture (tcpdump)')
    parser.add_argument('--hz', type=int, default=100, help='Poll rate in Hz (poll mode only)')
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[ERROR] Must run as root (sudo)")
        sys.exit(1)

    # Handle clean shutdown
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    # Configure CSI extraction
    if not configure_csi(args.interface, args.channel):
        print("[ERROR] Failed to configure CSI extraction")
        sys.exit(1)

    if args.mode == 'poll':
        poll_csi_loop(args.interface, args.port, args.hz)
    else:
        capture_pcap_loop(args.interface, args.port)


if __name__ == '__main__':
    main()
