"""
ESP32 CSI Source
=================
Receives live CSI frames from an ESP32 running Espressif's esp-csi firmware
(csi_recv_router example) connected via USB serial.

The ESP32 outputs CSV lines prefixed with "CSI_DATA" containing per-frame
CSI as space-separated signed integers (interleaved I/Q pairs for each
subcarrier).

ESP32 CSI_DATA format (comma-separated fields):
  CSI_DATA, mac, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing,
  not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor, ampdu_cnt,
  channel, secondary_channel, local_timestamp, ant, sig_len, rx_state,
  len, first_word, "data[0] data[1] ... data[N-1]"

The data field contains interleaved [imag, real] int8 pairs for each subcarrier.
LLTF CSI typically has 64 subcarriers (128 int8 values).

References:
  - https://github.com/espressif/esp-csi
  - ESP-IDF Wi-Fi CSI documentation
"""

import numpy as np
import serial
import time
import threading
import logging
from collections import deque

from engine.csi_processor import CSIFrame, N_SUBCARRIERS, CSI_SAMPLE_RATE

logger = logging.getLogger(__name__)

# ESP32 LLTF provides 64 subcarriers; we resample to match our pipeline's 56
ESP32_SUBCARRIERS = 64


class ESP32CSISource:
    """
    Receives live CSI frames over USB serial from an ESP32 running csi_recv_router.

    Usage:
        source = ESP32CSISource(serial_port='/dev/ttyUSB0')
        frames = source.generate_frames(512)  # returns list[CSIFrame]
    """

    def __init__(self, serial_port='/dev/ttyUSB0', baud_rate=115200, buffer_seconds=10):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
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

        # Start the serial reader thread
        self._running = True
        self._thread = threading.Thread(target=self._serial_loop, daemon=True)
        self._thread.start()

    # ── Public interface (matches SimulatedCSISource / NexmonCSISource) ────────

    def generate_frames(self, window_size):
        """
        Return up to `window_size` most recent CSI frames.
        Called by the detection loop in server.py.
        """
        with self._lock:
            frames = list(self._frame_buffer)

        if len(frames) > window_size:
            frames = frames[-window_size:]
        return frames

    @property
    def active_signal_count(self):
        """Approximate number of distinct signal sources."""
        with self._lock:
            n = len(self._frame_buffer)
        if n == 0:
            return 0
        age = time.time() - (self._last_packet_time or self._start_time)
        if age > 5.0:
            return 0  # stale — ESP32 probably disconnected
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
            'serial_port': self.serial_port,
        }

    # ── Serial reader ─────────────────────────────────────────────────────────

    def _serial_loop(self):
        """Background thread: read CSI_DATA lines from ESP32 serial and parse into CSIFrames."""
        while self._running:
            try:
                ser = serial.Serial(self.serial_port, self.baud_rate, timeout=2)
                logger.info(f"ESP32CSISource connected to {self.serial_port} @ {self.baud_rate}")
                print(f"[ESP32] Connected to {self.serial_port}")
            except serial.SerialException as e:
                logger.error(f"Serial open failed: {e}")
                print(f"[ESP32] Waiting for serial device {self.serial_port}...")
                time.sleep(3)
                continue

            try:
                while self._running:
                    try:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                    except serial.SerialException as e:
                        logger.error(f"Serial read error: {e}")
                        break

                    if not line.startswith('CSI_DATA'):
                        continue

                    frame = self._parse_csi_line(line)
                    if frame is None:
                        self._packets_dropped += 1
                        continue

                    self._packets_received += 1
                    self._last_packet_time = time.time()

                    with self._lock:
                        self._frame_buffer.append(frame)
            finally:
                ser.close()

            # If we get here, serial was lost — retry
            if self._running:
                print(f"[ESP32] Serial connection lost, reconnecting...")
                time.sleep(2)

    def _parse_csi_line(self, line: str):
        """
        Parse a CSI_DATA CSV line into a CSIFrame.
        Returns None if the line is malformed.

        The data field is a bracketed, comma-separated list of int8 values:
            "[92,64,5,0,-24,19,-26,22,...]"

        Structure after the 4-byte header:
            - 26 I/Q pairs: subcarriers -26 to -1 (LLTF lower)
            - ~9 null I/Q pairs: DC + guard bands (zeros)
            - 26 I/Q pairs: subcarriers +1 to +26 (LLTF upper)
            - Plus 1 boundary pair on each side of the null gap
        """
        try:
            # Find the bracketed data array within the CSV line
            bracket_start = line.find('[')
            bracket_end = line.rfind(']')
            if bracket_start < 0 or bracket_end < 0:
                return None

            data_str = line[bracket_start + 1:bracket_end]
            values = [int(x) for x in data_str.split(',')]

            # Skip 4-byte header (length, channel info, flags)
            if len(values) < 8:
                return None
            values = values[4:]

            # Parse interleaved [imaginary, real] int8 pairs
            n_pairs = len(values) // 2
            imag = np.array(values[0::2], dtype=np.float64)
            real = np.array(values[1::2], dtype=np.float64)

            # Remove null subcarriers (DC + guard bands — both imag and real are 0)
            mask = (np.abs(imag) + np.abs(real)) > 0
            imag = imag[mask]
            real = real[mask]

            if len(imag) < 10:
                return None

            amplitudes = np.sqrt(real**2 + imag**2)
            phases = np.arctan2(imag, real)

            # Resample to pipeline's N_SUBCARRIERS (56)
            n_active = len(amplitudes)
            if n_active != N_SUBCARRIERS:
                indices = np.linspace(0, n_active - 1, N_SUBCARRIERS).astype(int)
                amplitudes = amplitudes[indices]
                phases = phases[indices]

            # Normalize amplitudes to roughly [0, 1] range
            amp_max = amplitudes.max()
            if amp_max > 0:
                amplitudes = amplitudes / amp_max

            return CSIFrame(
                timestamp=time.time(),
                amplitudes=amplitudes,
                phases=phases,
            )

        except (ValueError, IndexError) as e:
            logger.debug(f"CSI_DATA parse error: {e}")
            return None

    def stop(self):
        """Shut down the serial reader thread."""
        self._running = False
        self._thread.join(timeout=3.0)
