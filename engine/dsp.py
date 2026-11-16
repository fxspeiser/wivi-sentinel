"""
CSI DSP primitives (analysis pipeline v2)
=========================================
Pure, stateless numpy/scipy functions used by `engine/analysis.py`.

These exist beside — not instead of — the estimators in `csi_processor.py`.
They address the measurement limits of the original chain:

  - frequency axes come from *measured* frame timestamps, not an assumed rate;
  - frequency resolution comes from observation time (long windows), with
    zero-padding + parabolic interpolation used only to refine a peak that the
    window can already resolve;
  - harmonic content is read from an unfiltered spectrum, so a band-pass can
    no longer delete the harmonic before it is measured;
  - quality is a spectral SNR in dB against the in-band noise floor, which
    does not saturate the way peak/mean of a band-passed signal does.

Nothing here keeps state, touches disk, or imports the rest of the engine,
so every function is unit-testable with synthetic signals.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as sig
from scipy.ndimage import median_filter

EPS = 1e-12


# ─── Timing ──────────────────────────────────────────────────────────────────

def measure_sample_rate(timestamps: np.ndarray, nominal_fs: float = 100.0) -> dict:
    """Estimate the real frame rate and its health from frame timestamps.

    Returns fs (Hz), jitter_ms (robust std of the inter-frame interval),
    gaps (intervals longer than 2.5x the median), loss_pct (estimated share of
    frames missing inside those gaps) and `valid` (False when the timestamps
    are unusable and `nominal_fs` was returned instead).
    """
    ts = np.asarray(timestamps, dtype=np.float64)
    fallback = {'fs': float(nominal_fs), 'jitter_ms': 0.0, 'gaps': 0,
                'loss_pct': 0.0, 'valid': False}
    if ts.size < 8:
        return fallback
    d = np.diff(ts)
    d = d[d > 0]
    if d.size < 6:
        return fallback
    med = float(np.median(d))
    if not np.isfinite(med) or med <= 0:
        return fallback
    mad = float(np.median(np.abs(d - med)))
    gap_mask = d > 2.5 * med
    missing = float(np.sum(np.round(d[gap_mask] / med) - 1.0)) if np.any(gap_mask) else 0.0
    return {
        'fs': 1.0 / med,
        'jitter_ms': 1.4826 * mad * 1000.0,
        'gaps': int(np.sum(gap_mask)),
        'loss_pct': 100.0 * missing / (d.size + missing),
        'valid': True,
    }


def resample_uniform(timestamps: np.ndarray, matrix: np.ndarray, fs: float):
    """Linearly interpolate (n, m) samples onto a uniform grid at `fs` Hz.

    Non-increasing timestamps are dropped first. Returns (t_uniform, X_uniform).
    Short gaps are bridged by interpolation; callers should consult
    `measure_sample_rate` for how much of the window that affected.
    """
    ts = np.asarray(timestamps, dtype=np.float64)
    X = np.asarray(matrix, dtype=np.float64)
    if X.ndim == 1:
        X = X[:, None]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    # A single backwards step can leave later duplicates; enforce monotonicity.
    while not np.all(np.diff(ts[keep]) > 0):
        idx = np.flatnonzero(keep)
        bad = idx[1:][np.diff(ts[idx]) <= 0]
        keep[bad] = False
    ts, X = ts[keep], X[keep]
    if ts.size < 2:
        return ts, X
    n = int(np.floor((ts[-1] - ts[0]) * fs)) + 1
    grid = ts[0] + np.arange(n) / fs
    out = np.empty((n, X.shape[1]), dtype=np.float64)
    for j in range(X.shape[1]):
        out[:, j] = np.interp(grid, ts, X[:, j])
    return grid, out


# ─── Denoising ───────────────────────────────────────────────────────────────

def hampel(matrix: np.ndarray, half_window: int = 5, n_sigma: float = 4.0):
    """Per-column Hampel-style outlier filter. Returns (filtered, n_replaced).

    A sample further than n_sigma robust-sigmas from its rolling median is
    replaced by that median. Removes AGC/packet spikes without smearing the
    surrounding samples the way a low-pass would. The robust sigma is one MAD
    per column (of the residual about the rolling median) rather than a rolling
    MAD: an order of magnitude cheaper, which matters on a Pi.
    """
    X = np.asarray(matrix, dtype=np.float64)
    if X.ndim == 1:
        X = X[:, None]
    if X.shape[0] < 2 * half_window + 1:
        return X.copy(), 0
    med = median_filter(X, size=(2 * half_window + 1, 1), mode='nearest')
    resid = np.abs(X - med)
    # Robust sigma: the larger of the residual MAD and a first-difference
    # estimate. On a smooth, low-noise signal the residual MAD collapses toward
    # zero and would flag every waveform peak; the difference term prevents that.
    sigma = np.maximum(1.4826 * np.median(resid, axis=0),
                       1.4826 * np.median(np.abs(np.diff(X, axis=0)), axis=0) / np.sqrt(2.0))
    outlier = resid > (n_sigma * sigma + EPS)
    return np.where(outlier, med, X), int(np.sum(outlier))


def pca(matrix: np.ndarray, k: int = 3):
    """Top-k principal components of a (n, m) matrix.

    Returns (scores (n, k), variance_ratio (k,), loadings (k, m)). Scores carry
    their singular value, so component 0 is the dominant motion waveform.
    """
    X = np.asarray(matrix, dtype=np.float64)
    Xc = X - X.mean(axis=0)
    k = max(1, min(k, min(Xc.shape)))
    try:
        U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    except np.linalg.LinAlgError:
        mean_sig = Xc.mean(axis=1, keepdims=True)
        return mean_sig, np.ones(1), np.ones((1, X.shape[1])) / X.shape[1]
    total = float(np.sum(S ** 2)) + EPS
    return U[:, :k] * S[:k], (S[:k] ** 2) / total, Vt[:k]


def block_decimate(matrix: np.ndarray, factor: int) -> np.ndarray:
    """Decimate along time by averaging non-overlapping blocks (boxcar anti-alias)."""
    X = np.asarray(matrix, dtype=np.float64)
    if factor <= 1:
        return X
    n = (X.shape[0] // factor) * factor
    if n == 0:
        return X[:0]
    return X[:n].reshape(n // factor, factor, *X.shape[1:]).mean(axis=1)


def highpass(matrix: np.ndarray, fs: float, cutoff_hz: float = 0.3) -> np.ndarray:
    """Zero-phase 2nd-order Butterworth high-pass along time."""
    X = np.asarray(matrix, dtype=np.float64)
    wn = cutoff_hz / (fs / 2.0)
    if not (0 < wn < 1) or X.shape[0] < 16:
        return X - X.mean(axis=0)
    b, a = sig.butter(2, wn, btype='high')
    return sig.filtfilt(b, a, X, axis=0)


def bandpass(matrix: np.ndarray, fs: float, band) -> np.ndarray:
    """Zero-phase 3rd-order Butterworth band-pass along time."""
    X = np.asarray(matrix, dtype=np.float64)
    nyq = fs / 2.0
    lo, hi = band[0] / nyq, min(band[1] / nyq, 0.99)
    if not (0 < lo < hi) or X.shape[0] < 32:
        return X - X.mean(axis=0)
    b, a = sig.butter(3, [lo, hi], btype='band')
    return sig.filtfilt(b, a, X, axis=0)


# ─── Spectral estimation ─────────────────────────────────────────────────────

def periodogram(x: np.ndarray, fs: float, pad_factor: int = 8):
    """Hann-windowed, zero-padded periodogram of a detrended 1-D signal.

    Zero-padding only interpolates the spectrum; the resolvable spacing is
    still ~1/duration. Returns (freqs, power).
    """
    x = sig.detrend(np.asarray(x, dtype=np.float64), type='linear')
    n = x.size
    nfft = int(2 ** np.ceil(np.log2(max(n, 8)))) * max(1, pad_factor)
    spec = np.fft.rfft(x * np.hanning(n), nfft)
    return np.fft.rfftfreq(nfft, 1.0 / fs), (np.abs(spec) ** 2) / max(n, 1)


def _parabolic_peak(freqs: np.ndarray, power: np.ndarray, idx: int) -> float:
    """Refine a spectral peak location with a 3-point parabola on log power."""
    if idx <= 0 or idx >= power.size - 1:
        return float(freqs[idx])
    a, b, c = (np.log(power[idx - 1] + EPS), np.log(power[idx] + EPS),
               np.log(power[idx + 1] + EPS))
    denom = a - 2 * b + c
    if abs(denom) < EPS:
        return float(freqs[idx])
    delta = float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))
    return float(freqs[idx] + delta * (freqs[1] - freqs[0]))


def estimate_rate(x: np.ndarray, fs: float, band, min_seconds: float) -> dict:
    """Estimate the dominant periodicity of `x` inside `band` (Hz).

    Returns hz, per_min, snr_db (peak over the median in-band floor),
    resolution_hz (1/duration — what the window can physically resolve),
    acf_agrees (an autocorrelation estimate lands within 12% of the spectral
    one) and confidence in [0, 1]. Confidence is zero below ~6 dB, which is
    where a peak in band-limited noise typically sits, and is scaled down
    when the window is shorter than `min_seconds`.
    """
    x = np.asarray(x, dtype=np.float64)
    duration = x.size / fs if fs > 0 else 0.0
    empty = {'hz': 0.0, 'per_min': 0.0, 'snr_db': 0.0, 'confidence': 0.0,
             'resolution_hz': 0.0, 'acf_agrees': False, 'window_s': round(duration, 1)}
    if x.size < 16 or duration * band[0] < 1.5:   # need >=1.5 cycles of the slowest rate
        return empty
    freqs, power = periodogram(x, fs)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if np.count_nonzero(mask) < 3 or float(np.max(power[mask])) <= 0:
        return empty
    in_band = np.flatnonzero(mask)
    peak_idx = int(in_band[np.argmax(power[mask])])
    hz = _parabolic_peak(freqs, power, peak_idx)
    floor = float(np.median(power[mask])) + EPS
    snr_db = 10.0 * np.log10(float(power[peak_idx]) / floor)

    # Autocorrelation cross-check, computed from the band-limited spectrum
    # (Wiener–Khinchin), so it sees exactly the band the peak was picked from.
    acf = np.fft.irfft(np.where(mask, power, 0.0))
    lag_lo = max(1, int(np.floor(fs_lag(freqs) / band[1])))
    lag_hi = min(acf.size // 2 - 1, int(np.ceil(fs_lag(freqs) / band[0])))
    acf_agrees = False
    if lag_hi > lag_lo + 1:
        lag = lag_lo + int(np.argmax(acf[lag_lo:lag_hi + 1]))
        acf_hz = fs_lag(freqs) / lag
        acf_agrees = abs(acf_hz - hz) <= 0.12 * hz

    confidence = float(np.clip((snr_db - 6.0) / 12.0, 0.0, 1.0))
    confidence *= 1.0 if acf_agrees else 0.6
    confidence *= float(np.clip(duration / max(min_seconds, EPS), 0.0, 1.0))
    return {
        'hz': round(hz, 4), 'per_min': round(hz * 60.0, 1),
        'snr_db': round(float(snr_db), 1), 'confidence': round(confidence, 3),
        'resolution_hz': round(1.0 / duration, 4), 'acf_agrees': bool(acf_agrees),
        'window_s': round(duration, 1),
    }


def fs_lag(freqs: np.ndarray) -> float:
    """Sample rate implied by an rfft frequency axis (lag-to-Hz conversion)."""
    return float(freqs[-1] * 2.0)


def harmonic_ratio(x: np.ndarray, fs: float, f0: float) -> float:
    """Power at 2*f0 divided by power at f0, from an *unfiltered* spectrum.

    The original chain measured this after a 0.8–4 Hz band-pass, which removes
    the second harmonic of any cadence above 2 Hz before it can be counted.
    Returns 0.0 when f0 is unusable or 2*f0 is above Nyquist.
    """
    if f0 <= 0 or 2.0 * f0 >= fs / 2.0:
        return 0.0
    freqs, power = periodogram(x, fs)
    half_width = max(0.15 * f0, 1.5 * fs / max(len(x), 1))
    fund = power[(freqs >= f0 - half_width) & (freqs <= f0 + half_width)]
    harm = power[(freqs >= 2 * f0 - half_width) & (freqs <= 2 * f0 + half_width)]
    if fund.size == 0 or harm.size == 0:
        return 0.0
    return float(np.sum(harm) / (np.sum(fund) + EPS))


def motion_spectrogram(components: np.ndarray, fs: float, window_s: float = 0.64,
                       overlap: float = 0.875):
    """Summed STFT power of the given component waveforms.

    This is an *amplitude* spectrogram: it shows how fast the channel is
    changing, which tracks limb/torso speed, but it carries no sign — it
    cannot tell approach from recede — and anything faster than fs/2 aliases.
    Returns (freqs, times, power (n_freqs, n_times)).
    """
    C = np.asarray(components, dtype=np.float64)
    if C.ndim == 1:
        C = C[:, None]
    nperseg = int(2 ** np.round(np.log2(max(16.0, window_s * fs))))
    nperseg = min(nperseg, C.shape[0])
    if nperseg < 16:
        return np.zeros(0), np.zeros(0), np.zeros((0, 0))
    noverlap = min(nperseg - 1, int(nperseg * overlap))
    total = None
    for j in range(C.shape[1]):
        f, t, s = sig.spectrogram(C[:, j], fs=fs, window='hann', nperseg=nperseg,
                                  noverlap=noverlap, detrend='constant', mode='psd')
        total = s if total is None else total + s
    return f, t, total


# ─── Motion gate ─────────────────────────────────────────────────────────────

def motion_index(matrix: np.ndarray) -> float:
    """Dimensionless activity level of a (n, m) window: the median over
    subcarriers of (std after linear detrend) / (mean level).

    Detrending removes slow drift so the index reflects movement inside the
    window; dividing by the level makes it insensitive to absolute gain.
    """
    X = np.asarray(matrix, dtype=np.float64)
    if X.shape[0] < 8:
        return 0.0
    resid = sig.detrend(X, axis=0, type='linear')
    level = np.abs(X.mean(axis=0)) + EPS
    return float(np.median(resid.std(axis=0) / level))


def presence_index(matrix: np.ndarray, fs: float, target_fs: float = 5.0) -> float:
    """Slow-motion counterpart of `motion_index` for a longer window.

    Averaging down to ~5 Hz first suppresses per-frame noise, which exposes
    the small, slow disturbance of a stationary breathing body.
    """
    slow = block_decimate(matrix, max(1, int(round(fs / target_fs))))
    return motion_index(slow)


def classify_motion(fast_ratio: float, slow_ratio: float, moving_ratio: float = 4.0,
                    still_ratio: float = 2.0) -> str:
    """Map activity ratios (index / empty-room floor) to a motion state.

    'moving' — gross motion in the last couple of seconds (walking)
    'still'  — no gross motion, but a slow disturbance above the floor
               (a present, stationary body)
    'quiet'  — indistinguishable from the floor
    """
    if fast_ratio >= moving_ratio:
        return 'moving'
    return 'still' if (slow_ratio >= still_ratio or fast_ratio >= still_ratio) else 'quiet'
