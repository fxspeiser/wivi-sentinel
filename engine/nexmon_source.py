"""
Nexmon CSI Source
==================
Receives live CSI frames from a Raspberry Pi 4 running Nexmon CSI firmware.

The Pi extracts CSI from the Broadcom BCM43455c0 WiFi chip and sends raw
UDP packets containing per-frame amplitude and phase data across 56 subcarriers
(802.11n 20 MHz channel).

Packet format (Nexmon CSI extractor):
  - 18-byte header: magic(2) + rssi(1) + fc(1) + src_mac(6) + seq(2) +
                     core(1) + spatial(1) + chanspec(2) + chip_version(2)
  - Payload: N_SUBCARRIERS * 4 bytes (interleaved int16 real/imag pairs)

References:
  - https://github.com/seemoo-lab/nexmon_csi
  - Gringoli et al., "Free Your CSI", WiNTECH 2019
"""

import numpy as np
import socket
import struct
import time
import threading
import logging
from collections import deque

from engine.csi_processor import CSIFrame, N_SUBCARRIERS, CSI_SAMPLE_RATE

logger = logging.getLogger(__name__)

# Nexmon packet header size (bytes)
NEXMON_HEADER_SIZE = 18
# Each subcarrier is a complex int16 pair (real, imag) = 4 bytes
SUBCARRIER_BYTES = N_SUBCARRIERS * 4
# Expected minimum packet size
MIN_PACKET_SIZE = NEXMON_HEADER_SIZE + SUBCARRIER_BYTES


class NexmonCSISource:
    """
    Receives live CSI frames over UDP from a Nexmon-enabled Raspberry Pi 4.

    Usage:
        source = NexmonCSISource(udp_port=5500)
        frames = source.generate_frames(512)  # returns list[CSIFrame]
    """

    def __init__(self, udp_port=5500, buffer_seconds=10):
        self.udp_port = udp_port
        self.n_subcarriers = N_SUBCARRIERS
        self.sample_rate = CSI_SAMPLE_RATE

        # Ring buffer holds up to buffer_seconds worth of frames
        self._max_frames = CSI_SAMPLE_RATE * buffer_seconds
        self._frame_buffer = deque(maxlen=self._max_frames)
        self._lock = threading.Lock()

        # Stats
        self._packets_received = 0
        self._packets_dropped = 0
        self._last_packet_time = None
        self._start_time = time.time()

        # Start the UDP listener thread
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    # ── Public interface (matches SimulatedCSISource) ───────────────────────

    def generate_frames(self, window_size):
        """
        Return up to `window_size` most recent CSI frames.
        Called by the detection loop in server.py.
        """
        with self._lock:
            frames = list(self._frame_buffer)

        # Return the most recent window_size frames
        if len(frames) > window_size:
            frames = frames[-window_size:]
        return frames

    @property
    def active_signal_count(self):
        """Approximate number of distinct signal sources (based on recent frame rate)."""
        with self._lock:
            n = len(self._frame_buffer)
        if n == 0:
            return 0
        # If we're receiving frames, report at least 1 active source
        age = time.time() - (self._last_packet_time or self._start_time)
        if age > 5.0:
            return 0  # stale — Pi probably disconnected
        return max(1, n // self.sample_rate)

    def get_status_info(self):
        """Extra status dict for the /api/status endpoint."""
        return {
            'packets_received': self._packets_received,
            'packets_dropped': self._packets_dropped,
            'buffer_fill': len(self._frame_buffer),
            'buffer_max': self._max_frames,
            'last_packet_age': (
                round(time.time() - self._last_packet_time, 2)
                if self._last_packet_time else None
            ),
            'udp_port': self.udp_port,
        }

    # ── UDP listener ────────────────────────────────────────────────────────

    def _listen_loop(self):
        """Background thread: receive Nexmon UDP packets and parse into CSIFrames."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Increase receive buffer to reduce drops under burst traffic
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        except OSError:
            pass

        sock.bind(('0.0.0.0', self.udp_port))
        sock.settimeout(2.0)  # allow clean shutdown
        logger.info(f"NexmonCSISource listening on UDP :{self.udp_port}")

        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as e:
                logger.error(f"UDP recv error: {e}")
                continue

            frame = self._parse_packet(data)
            if frame is None:
                self._packets_dropped += 1
                continue

            self._packets_received += 1
            self._last_packet_time = time.time()

            with self._lock:
                self._frame_buffer.append(frame)

        sock.close()

    def _parse_packet(self, data: bytes):
        """
        Parse a Nexmon CSI UDP packet into a CSIFrame.
        Returns None if the packet is malformed.
        """
        if len(data) < MIN_PACKET_SIZE:
            return None

        try:
            # Skip the 18-byte Nexmon header
            payload = data[NEXMON_HEADER_SIZE:]

            # Parse interleaved int16 real/imag pairs
            n_values = self.n_subcarriers * 2
            if len(payload) < n_values * 2:
                return None

            iq = struct.unpack(f'<{n_values}h', payload[:n_values * 2])

            real = np.array(iq[0::2], dtype=np.float64)
            imag = np.array(iq[1::2], dtype=np.float64)

            amplitudes = np.sqrt(real**2 + imag**2)
            phases = np.arctan2(imag, real)

            # Normalize amplitudes to roughly [0, 1] range
            amp_max = amplitudes.max()
            if amp_max > 0:
                amplitudes = amplitudes / amp_max

            return CSIFrame(
                timestamp=time.time(),
                amplitudes=amplitudes,
                phases=phases,
            )

        except (struct.error, ValueError) as e:
            logger.debug(f"Packet parse error: {e}")
            return None

    def stop(self):
        """Shut down the listener thread."""
        self._running = False
        self._thread.join(timeout=3.0)
