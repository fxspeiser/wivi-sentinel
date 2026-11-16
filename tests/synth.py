"""Synthetic CSI for tests: walkers with distinct, repeatable gait signatures."""

import numpy as np

N_SUB = 52


class Frame:
    """Minimal stand-in for engine.csi_processor.CSIFrame."""

    def __init__(self, timestamp, amplitudes, rssi=-40.0, device_ts=None, raw_amplitudes=None):
        self.timestamp = timestamp
        self.amplitudes = amplitudes
        self.phases = np.zeros_like(amplitudes)
        self.rssi = rssi
        self.device_ts = device_ts
        self.raw_amplitudes = raw_amplitudes


def person(seed: int) -> dict:
    """A walker: cadence, harmonic mix, limb-band energy and a subcarrier footprint."""
    rng = np.random.default_rng(1000 + seed)
    return {
        'cadence_hz': 1.5 + 0.22 * seed,
        'harmonic': 0.2 + 0.15 * (seed % 4),
        'limb_hz': 9.0 + 3.0 * seed,
        'limb_gain': 0.15 + 0.1 * (seed % 3),
        'footprint': rng.dirichlet(np.ones(N_SUB) * 1.5) * N_SUB,
    }


def walk(p: dict, seconds: float, fs: float = 100.0, seed: int = 0, room_seed: int = 0,
         noise: float = 0.01) -> np.ndarray:
    """(n, N_SUB) amplitude matrix of `p` walking. `room_seed` changes the
    static channel and perturbs the footprint, standing in for a new session."""
    rng = np.random.default_rng(seed)
    room = np.random.default_rng(500 + room_seed)
    static = 1.0 + 0.3 * room.random(N_SUB)
    footprint = p['footprint'] * (1.0 + 0.15 * room.normal(size=N_SUB))
    t = np.arange(int(seconds * fs)) / fs
    f0 = p['cadence_hz'] * (1.0 + 0.02 * rng.normal())
    phase = rng.uniform(0, 2 * np.pi)
    gait = (np.sin(2 * np.pi * f0 * t + phase)
            + p['harmonic'] * np.sin(2 * np.pi * 2 * f0 * t + 0.5 + phase)
            + p['limb_gain'] * np.sin(2 * np.pi * p['limb_hz'] * t) * (1 + np.sin(2 * np.pi * f0 * t)))
    X = static[None, :] + 0.08 * gait[:, None] * footprint[None, :]
    return X + noise * rng.normal(size=X.shape)


def still(seconds: float, fs: float = 100.0, resp_hz: float = 0.25, heart_hz: float = 1.2,
          seed: int = 0, noise: float = 0.003) -> np.ndarray:
    """A stationary breathing subject."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    footprint = rng.dirichlet(np.ones(N_SUB) * 2) * N_SUB
    body = 0.02 * np.sin(2 * np.pi * resp_hz * t) + 0.004 * np.sin(2 * np.pi * heart_hz * t)
    return 1.0 + body[:, None] * footprint[None, :] + noise * rng.normal(size=(t.size, N_SUB))


def empty(seconds: float, fs: float = 100.0, seed: int = 0, noise: float = 0.003) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 1.0 + noise * rng.normal(size=(int(seconds * fs), N_SUB))


def frames(matrix: np.ndarray, fs: float = 100.0, t0: float = 0.0, jitter: float = 0.0,
           seed: int = 0) -> list:
    rng = np.random.default_rng(seed)
    ts = t0 + np.arange(matrix.shape[0]) / fs
    if jitter:
        ts = ts + rng.uniform(-jitter, jitter, size=ts.size)
    return [Frame(float(t), row) for t, row in zip(ts, matrix)]
