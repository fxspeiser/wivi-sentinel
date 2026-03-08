"""
Nexmon CSI Source — Mac-side UDP Receiver
==========================================
Receives CSI frames from the Raspberry Pi 4's csi_extractor.py over UDP
and converts them to CSIFrame objects for the signal processing engine.

Wire protocol (from Pi):
  Bytes 0-3:    magic (0x57564353 = "WVCS")
  Bytes 4-7:    sequence number (uint32 LE)
  Bytes 8-15:   timestamp (uint64 LE, microseconds since epoch)
  Bytes 16-17:  n_subcarriers (uint16 LE)
  Bytes 18-19:  rssi (int16 LE)
  Bytes 20-21:  channel (uint16 LE)
  Bytes 22-23:  bandwidth (uint16 LE)
  Bytes 24+:    CSI data as float32 pairs [amp0, phase0, amp1, phase1, ...]

Drop-in replacement for SimulatedCSISource:
  # In server.py:
  from engine.nexmon_source import NexmonCSISource
  csi_source = NexmonCSISource(udp_port=5500)
"""

import socket
import struct
import time
import threading
import logging
import numpy as np
from collections import deque
from scipy.interpolate import interp1d

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from engine.csi_processor import CSIFrame, N_SUBCARRIERS, CSI_SAMPLE_RATE

logger = logging.getLogger('nexmon_source')

WVCS_MAGIC = 0x57564353
HEADER_LEN = 24  # 4+4+8+2+2+2+2


class NexmonCSISource:
    """
    Live CSI source receiving from a Nexmon-patched Raspberry Pi 4 over UDP.

    Drop-in compatible with SimulatedCSISource — implements generate_frames().
    Also exposes .people (empty list) for server.py compatibility.
    """

    def __init__(self, udp_port=5500, bind_addr='0.0.0.0',
                 target_subcarriers=N_SUBCARRIERS,
                 buffer_seconds=10, timeout=2.0):
        """
        Args:
            udp_port: UDP port to listen on (must match Pi extractor config)
            bind_addr: Bind address (0.0.0.0 for all interfaces)
            target_subcarriers: Resample all frames to this subcarrier count
            buffer_seconds: How many seconds of frames to keep in ring buffer
            timeout: Socket read timeout
        """
        self.udp_port = udp_port
        self.target_subcarriers = target_subcarriers
        self.timeout = timeout

        # For server.py compatibility (SimulatedCSISource has .people)
        self.people = []

        # Ring buffer: stores parsed CSIFrame objects
        max_frames = int(buffer_seconds * CSI_SAMPLE_RATE)
        self._buffer = deque(maxlen=max(max_frames, 2048))
        self._buffer_lock = threading.Lock()

        # Stats
        self.packets_received = 0
        self.packets_dropped = 0
        self.parse_errors = 0
        self.last_packet_time = 0
        self.last_seq = -1
        self.pi_connected = False
        self.source_bandwidth = 0
        self.source_channel = 0
        self.avg_rssi = -100

        # RSSI history for active signal count estimation
        self._rssi_history = deque(maxlen=100)

        # Socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Increase receive buffer to handle bursts
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        except:
            pass
        self.sock.settimeout(timeout)
        self.sock.bind((bind_addr, udp_port))

        self._running = True

        # Start listener thread
        self._listener = threading.Thread(
            target=self._listen_loop, daemon=True, name='nexmon-rx'
        )
        self._listener.start()

        # Start stats thread
        self._stats = threading.Thread(
            target=self._stats_loop, daemon=True, name='nexmon-stats'
        )
        self._stats.start()

        logger.info(
            f"NexmonCSISource listening on {bind_addr}:{udp_port} "
            f"(target {target_subcarriers} subcarriers)"
        )

    def _listen_loop(self):
        """Background: continuously receive and buffer CSI packets."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(4096)
                frame = self._parse_packet(data)
                if frame is not None:
                    with self._buffer_lock:
                        self._buffer.append(frame)
                    self.packets_received += 1
                    self.last_packet_time = time.time()
                    self.pi_connected = True
                else:
                    self.parse_errors += 1
            except socket.timeout:
                # Check if Pi connection dropped
                if self.last_packet_time > 0 and time.time() - self.last_packet_time > 5:
                    self.pi_connected = False
                continue
            except Exception as e:
                self.parse_errors += 1
                if self.parse_errors % 500 == 1:
                    logger.warning(f"Listener error #{self.parse_errors}: {e}")

    def _parse_packet(self, data: bytes):
        """Parse one WVCS UDP packet into a CSIFrame."""
        if len(data) < HEADER_LEN:
            return None

        # Parse header
        magic, seq, ts_us, n_sc, rssi, channel, bw = struct.unpack(
            '<IIQHHH H', data[:HEADER_LEN]
        )

        if magic != WVCS_MAGIC:
            return None

        if n_sc < 4 or n_sc > 512:
            return None

        # Check for dropped packets
        if self.last_seq >= 0:
            expected = (self.last_seq + 1) & 0xFFFFFFFF
            if seq != expected:
                dropped = (seq - expected) & 0xFFFFFFFF
                if dropped < 10000:  # Sanity check
                    self.packets_dropped += dropped
        self.last_seq = seq

        # Update source info
        self.source_bandwidth = bw
        self.source_channel = channel
        self._rssi_history.append(rssi if rssi < 0 else rssi - 256)  # Ensure signed

        # Parse CSI data: float32 pairs [amp, phase, amp, phase, ...]
        csi_offset = HEADER_LEN
        expected_bytes = n_sc * 2 * 4  # 2 floats per subcarrier, 4 bytes each
        if len(data) < csi_offset + expected_bytes:
            return None

        try:
            csi_raw = np.frombuffer(
                data[csi_offset:csi_offset + expected_bytes], dtype=np.float32
            )
            amplitudes = csi_raw[0::2].copy()
            phases = csi_raw[1::2].copy()
        except (ValueError, IndexError):
            return None

        # Resample to target subcarrier count if needed
        if len(amplitudes) != self.target_subcarriers:
            amplitudes = self._resample(amplitudes, self.target_subcarriers)
            phases = self._resample(phases, self.target_subcarriers)

        # Timestamp from Pi (microseconds) or local time as fallback
        timestamp = ts_us / 1_000_000.0 if ts_us > 0 else time.time()

        return CSIFrame(
            timestamp=timestamp,
            amplitudes=amplitudes.astype(np.float64),
            phases=phases.astype(np.float64),
        )

    @staticmethod
    def _resample(data: np.ndarray, target_len: int) -> np.ndarray:
        """Resample 1D array to target length using linear interpolation."""
        if len(data) == target_len:
            return data
        if len(data) < 2:
            return np.zeros(target_len)
        x_old = np.linspace(0, 1, len(data))
        x_new = np.linspace(0, 1, target_len)
        return interp1d(x_old, data, kind='linear', fill_value='extrapolate')(x_new)

    def _stats_loop(self):
        """Periodic stats logging."""
        while self._running:
            time.sleep(15)
            if self.packets_received > 0:
                drop_rate = self.packets_dropped / max(self.packets_received + self.packets_dropped, 1)
                rssi_avg = np.mean(self._rssi_history) if self._rssi_history else -100
                self.avg_rssi = float(rssi_avg)
                logger.info(
                    f"Nexmon: rx={self.packets_received} dropped={self.packets_dropped} "
                    f"({drop_rate:.1%}) errors={self.parse_errors} "
                    f"RSSI={rssi_avg:.0f}dBm ch={self.source_channel} "
                    f"bw={self.source_bandwidth}MHz buf={len(self._buffer)}"
                )

    # ─── Public API (compatible with SimulatedCSISource) ─────────────────

    def generate_frames(self, n_frames: int) -> list:
        """
        Return up to n_frames from the buffer.

        If fewer frames are available, returns what's there.
        If buffer is empty, blocks briefly and retries.
        """
        frames = []
        retries = 3

        for attempt in range(retries):
            with self._buffer_lock:
                available = len(self._buffer)
                take = min(n_frames, available)
                for _ in range(take):
                    frames.append(self._buffer.popleft())

            if len(frames) >= n_frames:
                break

            if len(frames) == 0 and attempt < retries - 1:
                # Wait for more data
                time.sleep(0.1)

        return frames

    def toggle_person(self, idx):
        """No-op for compatibility with SimulatedCSISource."""
        pass

    @property
    def active_signal_count(self):
        """Estimate number of active signals based on RSSI variance."""
        if not self.pi_connected:
            return 0
        # If we're receiving data, at least 1 signal is present
        # Variance in RSSI suggests multiple moving bodies
        if len(self._rssi_history) < 10:
            return 1 if self.pi_connected else 0
        rssi_var = np.var(list(self._rssi_history))
        # Higher variance → more bodies disturbing the signal
        if rssi_var > 10:
            return 3
        elif rssi_var > 4:
            return 2
        return 1

    def get_status_info(self) -> dict:
        """Extra status info for the API."""
        return {
            'pi_connected': self.pi_connected,
            'packets_received': self.packets_received,
            'packets_dropped': self.packets_dropped,
            'drop_rate': round(self.packets_dropped / max(self.packets_received + self.packets_dropped, 1), 4),
            'parse_errors': self.parse_errors,
            'avg_rssi': round(self.avg_rssi, 1),
            'source_channel': self.source_channel,
            'source_bandwidth': self.source_bandwidth,
            'buffer_depth': len(self._buffer),
            'last_packet_age': round(time.time() - self.last_packet_time, 1) if self.last_packet_time > 0 else -1,
        }

    def close(self):
        """Clean shutdown."""
        self._running = False
        try:
            self.sock.close()
        except:
            pass