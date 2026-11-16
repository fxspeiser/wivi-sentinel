"""
Analysis engine (pipeline v2)
=============================
Keeps a long, timestamped ring buffer of CSI frames and derives from it
everything the Signal Lab dashboard and the roster identifier need:

  - acquisition health (measured frame rate, jitter, loss, outliers)
  - a motion gate (quiet / still / moving)
  - respiration, cardiac and cadence estimates on windows long enough to
    resolve them, each with an SNR and a confidence
  - a motion spectrogram, a subcarrier heatmap and a high-resolution PSD
  - live identification against the enrolled roster

It runs in parallel with the original detection pipeline and never alters
that pipeline's inputs. `server.py` decides whether any of these measurements
are fed to the legacy classifiers (off by default).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import threading
import time
from collections import deque

import numpy as np
from scipy import signal as sig

from engine import dsp
from engine.identifier import (
    EnrollmentStore, RosterIdentifier, extract_features, windows_from_capture,
    FEATURE_WINDOW_S,
)

BUFFER_SECONDS = 90.0        # history kept for long-window estimators + captures
ANALYSIS_SECONDS = 32.0      # most recent span processed per snapshot
RESP_BAND = (0.1, 0.6)       # 6–36 breaths/min
HEART_BAND = (0.8, 2.5)      # 48–150 BPM
CADENCE_BAND = (0.8, 4.0)    # 48–240 steps/min
RESP_WINDOW_S, HEART_WINDOW_S, CADENCE_WINDOW_S = 30.0, 15.0, 10.0
MOTION_WINDOW_S = 2.0       # gross-motion (walking) window
PRESENCE_WINDOW_S = 10.0    # slow-disturbance (stationary body) window
MAX_IMAGE_COLUMNS = 240
SPECTROGRAM_RANGE_DB = 45.0
MAX_CAPTURE_SECONDS = 60.0
_LABEL_RE = re.compile(r'^[\w .\-]{1,40}$')


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=np.uint8).tobytes()).decode('ascii')


def _pool_columns(image: np.ndarray, max_cols: int, how: str) -> np.ndarray:
    """Reduce the time axis (axis 1) of an image to at most `max_cols` columns."""
    n = image.shape[1]
    if n <= max_cols:
        return image
    factor = int(np.ceil(n / max_cols))
    usable = (n // factor) * factor
    blocks = image[:, n - usable:].reshape(image.shape[0], usable // factor, factor)
    return blocks.max(axis=2) if how == 'max' else blocks.mean(axis=2)


class AnalysisEngine:
    """Thread-safe: `ingest` may be called from several threads; `snapshot`
    is cached so concurrent dashboard clients share one computation."""

    def __init__(self, data_dir: str, nominal_fs: float = 100.0, wavelength_m: float = 0.1218):
        self.nominal_fs = float(nominal_fs)
        self.wavelength_m = float(wavelength_m)
        self.data_dir = data_dir
        self._buf_lock = threading.Lock()
        self._snap_lock = threading.Lock()
        self._frames: deque = deque()          # (ts, amplitudes float32, rssi)
        self._last_ts = -np.inf
        self._width = None
        self._width_mismatch = 0
        self._frames_ingested = 0

        self._cached = None
        self._cached_at = 0.0
        self._motion_history: deque = deque(maxlen=240)
        self._spec_ref_db = None
        self._heat_ref = None
        self._identity_timeline: deque = deque(maxlen=120)
        self._quiet_since = None

        self._state_path = os.path.join(data_dir, 'analysis_state.json')
        saved = self._load_state().get('motion_floor')
        self._baseline_floor = (tuple(saved) if isinstance(saved, list) and len(saved) == 2
                                else None)

        self.enrollment = EnrollmentStore(os.path.join(data_dir, 'enrollment.json'))
        self.identifier = RosterIdentifier(self.enrollment)
        self._capture = None                   # active labelled capture, if any
        self._last_capture = None

    # ── Ingest ──────────────────────────────────────────────────────────────

    def ingest(self, frames) -> int:
        """Append frames newer than anything already buffered. Returns count added.

        Prefers the capture device's own timestamp (`device_ts`) over host
        arrival time, which carries USB/UART batching jitter. Sources whose
        `generate_frames` returns overlapping windows are de-duplicated here.
        """
        added = 0
        with self._buf_lock:
            for f in frames:
                ts = getattr(f, 'device_ts', None)
                if ts is None:
                    ts = f.timestamp
                if ts <= self._last_ts:
                    continue
                amps = getattr(f, 'raw_amplitudes', None)
                if amps is None:
                    amps = f.amplitudes
                width = len(amps)
                if self._width is None:
                    self._width = width
                elif width != self._width:
                    # Tolerate the odd malformed frame; re-key only if the
                    # stream has really changed shape.
                    self._width_mismatch += 1
                    if self._width_mismatch < 50:
                        continue
                    self._frames.clear()
                    self._width = width
                self._width_mismatch = 0
                self._frames.append((float(ts), np.asarray(amps, dtype=np.float32),
                                     float(getattr(f, 'rssi', 0.0) or 0.0)))
                self._last_ts = ts
                added += 1
            horizon = self._last_ts - BUFFER_SECONDS
            while self._frames and self._frames[0][0] < horizon:
                self._frames.popleft()
            self._frames_ingested += added
        return added

    def _copy_buffer(self):
        with self._buf_lock:
            if not self._frames:
                return np.zeros(0), np.zeros((0, 0)), np.zeros(0)
            ts = np.fromiter((f[0] for f in self._frames), dtype=np.float64, count=len(self._frames))
            amps = np.stack([f[1] for f in self._frames]).astype(np.float64)
            rssi = np.fromiter((f[2] for f in self._frames), dtype=np.float64, count=len(self._frames))
        return ts, amps, rssi

    # ── Snapshot ────────────────────────────────────────────────────────────

    def snapshot(self, max_age: float = 0.45, identity_cfg: dict | None = None) -> dict:
        """Full analysis of the current buffer, recomputed at most every `max_age` s."""
        with self._snap_lock:
            now = time.time()
            if self._cached is not None and now - self._cached_at < max_age:
                return self._cached
            try:
                result = self._compute(identity_cfg or {})
            except Exception as exc:                      # never take the server down
                result = {'ready': False, 'reason': f'analysis error: {exc}'}
            result['capture'] = self._advance_capture()
            result['generated_at'] = now
            self._cached, self._cached_at = result, now
            return result

    def _compute(self, identity_cfg: dict) -> dict:
        ts, amps, rssi = self._copy_buffer()
        if ts.size < 64:
            return {'ready': False, 'reason': 'Waiting for CSI frames…', 'frames': int(ts.size)}

        rate = dsp.measure_sample_rate(ts, self.nominal_fs)
        fs = float(np.clip(rate['fs'], 5.0, 2000.0))
        recent = ts >= ts[-1] - ANALYSIS_SECONDS
        _, X = dsp.resample_uniform(ts[recent], amps[recent], fs)
        if X.shape[0] < 64:
            return {'ready': False, 'reason': 'Buffer filling…', 'frames': int(ts.size)}
        X, n_outliers = dsp.hampel(X)
        n = X.shape[0]
        seconds = n / fs

        def tail(window_s):
            return X[-min(n, int(round(window_s * fs))):]

        # ── Motion gate ──
        m_fast = dsp.motion_index(tail(MOTION_WINDOW_S))
        m_slow = dsp.presence_index(tail(PRESENCE_WINDOW_S), fs)
        self._motion_history.append((m_fast, m_slow))
        calibrated = self._baseline_floor is not None
        floor = self._baseline_floor if calibrated else self._adaptive_floor()
        if floor:
            fast_ratio, slow_ratio = m_fast / floor[0], m_slow / floor[1]
            state = dsp.classify_motion(fast_ratio, slow_ratio)
        else:
            fast_ratio = slow_ratio = None
            state = 'unknown'

        # ── Gait band: PCA of the high-passed recent window ──
        gait_hp = dsp.highpass(tail(CADENCE_WINDOW_S), fs, 0.3)
        gait_scores, gait_var, _ = dsp.pca(gait_hp, k=3)
        cadence = dsp.estimate_rate(gait_scores[:, 0], fs, CADENCE_BAND, 8.0)
        h_ratio = dsp.harmonic_ratio(gait_scores[:, 0], fs, cadence['hz'])

        # ── Respiration: long window, decimated to ~10 Hz ──
        q = max(1, int(fs // 10))
        resp_x = dsp.block_decimate(tail(RESP_WINDOW_S), q)
        resp_fs = fs / q
        resp_pc, _, _ = dsp.pca(resp_x, k=1)
        resp = dsp.estimate_rate(resp_pc[:, 0], resp_fs, RESP_BAND, 20.0)

        # ── Cardiac: band-passed 15 s window (experimental on this hardware) ──
        heart_pc, _, _ = dsp.pca(dsp.bandpass(tail(HEART_WINDOW_S), fs, HEART_BAND), k=1)
        heart = dsp.estimate_rate(heart_pc[:, 0], fs, HEART_BAND, 10.0)

        moving, quiet = state == 'moving', state == 'quiet'
        vitals = {
            'respiration': self._gate(resp, blocked=moving or quiet,
                                      why='subject moving' if moving else 'no one detected'),
            'heart': self._gate(heart, blocked=moving or quiet,
                                why='subject moving' if moving else 'no one detected'),
            'cadence': self._gate(cadence, blocked=state in ('quiet', 'still'),
                                  why='no walking detected'),
            'harmonic_ratio': round(float(h_ratio), 3),
        }
        vitals['heart']['experimental'] = True
        # A "heartbeat" sitting on the step rate (or its half) is gait leaking
        # into the cardiac band, not a pulse.
        if heart['hz'] and cadence['hz'] and cadence['confidence'] >= 0.5 and any(
                abs(heart['hz'] - f) <= 0.08 * f for f in (cadence['hz'], cadence['hz'] / 2)):
            vitals['heart'].update(valid=False, gate_reason='matches step rate')

        result = {
            'ready': True,
            'health': {
                'fs_hz': round(fs, 2),
                'fs_nominal_hz': self.nominal_fs,
                'timestamps_valid': rate['valid'],
                'jitter_ms': round(rate['jitter_ms'], 2),
                'gaps': rate['gaps'],
                'loss_pct': round(rate['loss_pct'], 2),
                'buffer_s': round(float(ts[-1] - ts[0]), 1),
                'analysis_s': round(seconds, 1),
                'subcarriers': int(X.shape[1]),
                'outlier_pct': round(100.0 * n_outliers / X.size, 3),
                'pc1_variance_pct': round(100.0 * float(gait_var[0]), 1),
                'rssi_dbm': round(float(np.mean(rssi[recent])), 1) if np.any(rssi[recent]) else None,
                'nyquist_hz': round(fs / 2.0, 1),
                'max_unaliased_speed_mps': round(fs / 2.0 * self.wavelength_m / 2.0, 2),
                'frames_ingested': self._frames_ingested,
            },
            'motion': {
                'state': state,
                'index': round(m_fast, 6),
                'ratio': round(fast_ratio, 2) if fast_ratio is not None else None,
                'presence_ratio': round(slow_ratio, 2) if slow_ratio is not None else None,
                'calibrated': calibrated,
                'history': [round(v[0] / floor[0], 2) if floor else 0.0
                            for v in list(self._motion_history)[-120:]],
            },
            'vitals': vitals,
            'psd': self._psd_payload(X, fs, resp, heart, cadence),
            'spectrogram': self._spectrogram_payload(dsp.highpass(X, fs, 0.3), fs),
            'heatmap': self._heatmap_payload(X, fs),
            'identity': self._identify(X, fs, state, identity_cfg),
        }
        return result

    @staticmethod
    def _gate(estimate: dict, blocked: bool, why: str) -> dict:
        """Attach validity to an estimate. A gated estimate is still reported
        (for diagnostics) but flagged so nothing downstream mistakes it for a
        measurement."""
        out = dict(estimate)
        if blocked:
            out.update(valid=False, gated=True, gate_reason=why)
        elif estimate['confidence'] < 0.5:
            out.update(valid=False, gated=False,
                       gate_reason='peak too weak' if estimate['hz'] else 'window too short')
        else:
            out.update(valid=True, gated=False, gate_reason=None)
        return out

    def _adaptive_floor(self):
        """Without a captured baseline, assume the quietest tenth of recent
        history was an empty room. Good enough to gate; a baseline is better."""
        if len(self._motion_history) < 10:
            return None
        hist = np.array(self._motion_history)
        floor = np.percentile(hist, 10, axis=0)
        return (float(floor[0]), float(floor[1])) if np.all(floor > 0) else None

    # ── Image / plot payloads ───────────────────────────────────────────────

    def _psd_payload(self, X, fs, resp, heart, cadence) -> dict:
        """High-resolution 0–5 Hz spectrum of PC1, plus the original chain's
        256-sample Welch estimate of the same signal for comparison."""
        pc, _, _ = dsp.pca(X, k=1)
        x = pc[:, 0]
        freqs, power = dsp.periodogram(x, fs, pad_factor=4)
        f_max = min(5.0, fs / 2.0)
        grid = np.linspace(0.05, f_max, 200)
        hi_db = 10.0 * np.log10(np.interp(grid, freqs, power) + dsp.EPS)
        wf, wp = sig.welch(sig.detrend(x), fs=fs, nperseg=min(256, len(x)))
        keep = wf <= f_max
        lo_db = 10.0 * np.log10(wp[keep] + dsp.EPS)
        top = float(np.max(hi_db))
        return {
            'freqs': [round(float(f), 3) for f in grid],
            'db': [round(float(v - top), 1) for v in hi_db],
            'legacy_freqs': [round(float(f), 3) for f in wf[keep]],
            'legacy_db': [round(float(v - np.max(lo_db)), 1) for v in lo_db],
            'resolution_hz': round(fs / len(x), 4),
            'legacy_resolution_hz': round(fs / min(256, len(x)), 4),
            'bands': {'respiration': RESP_BAND, 'heart': HEART_BAND, 'cadence': CADENCE_BAND},
            'peaks': {'respiration': resp['hz'], 'heart': heart['hz'], 'cadence': cadence['hz']},
        }

    def _spectrogram_payload(self, hp, fs) -> dict:
        scores, _, _ = dsp.pca(hp, k=3)
        freqs, times, power = dsp.motion_spectrogram(scores, fs)
        if power.size == 0:
            return {'cols': 0, 'rows': 0, 'data': ''}
        db = 10.0 * np.log10(power + dsp.EPS)
        # Slowly-adapting reference level, so the colour scale does not pump
        # with every frame and a quiet room still looks quiet.
        peak = float(np.percentile(db, 99.5))
        self._spec_ref_db = peak if self._spec_ref_db is None else max(
            peak, 0.97 * self._spec_ref_db + 0.03 * peak)
        img = _pool_columns(db, MAX_IMAGE_COLUMNS, 'max')
        lo = self._spec_ref_db - SPECTROGRAM_RANGE_DB
        img = np.clip((img - lo) / SPECTROGRAM_RANGE_DB, 0.0, 1.0)
        return {
            'rows': int(img.shape[0]), 'cols': int(img.shape[1]),
            'data': _b64(np.round(img * 255)),            # row-major, row 0 = 0 Hz
            'f_max_hz': round(float(freqs[-1]), 2),
            'seconds': round(float(hp.shape[0] / fs), 1),
            'range_db': SPECTROGRAM_RANGE_DB,
            'hz_per_mps': round(2.0 / self.wavelength_m, 2),
        }

    def _heatmap_payload(self, X, fs) -> dict:
        """Per-subcarrier relative deviation from its own mean, over time."""
        rel = (X - X.mean(axis=0)) / (np.abs(X.mean(axis=0)) + dsp.EPS)
        scale = float(np.percentile(np.abs(rel), 98)) + dsp.EPS
        self._heat_ref = scale if self._heat_ref is None else max(
            scale, 0.95 * self._heat_ref + 0.05 * scale)
        img = _pool_columns(rel.T, MAX_IMAGE_COLUMNS, 'mean')        # (subcarrier, time)
        img = np.clip(img / self._heat_ref, -1.0, 1.0)
        return {
            'rows': int(img.shape[0]), 'cols': int(img.shape[1]),
            'data': _b64(np.round((img + 1.0) * 127.5)),   # 128 ≈ no change
            'seconds': round(float(X.shape[0] / fs), 1),
            'scale_pct': round(100.0 * self._heat_ref, 2),
        }

    # ── Identification ──────────────────────────────────────────────────────

    def _identify(self, X, fs, state, cfg) -> dict:
        report = self.identifier.report
        base = {'trained': bool(report.get('trained')), 'enabled': cfg.get('enabled', True),
                'timeline': list(self._identity_timeline)}
        if not base['enabled']:
            return {**base, 'status': 'disabled'}
        if not base['trained']:
            return {**base, 'status': 'untrained', 'reason': report.get('reason')}
        n_win = int(round(FEATURE_WINDOW_S * fs))
        if X.shape[0] < n_win:
            return {**base, 'status': 'warming_up'}
        if state == 'quiet':
            # Evidence from the last walk should not bleed into the next one.
            self._quiet_since = self._quiet_since or time.time()
            if time.time() - self._quiet_since > 3.0:
                self.identifier.reset_track()
            self._push_timeline(None)
            return {**base, 'status': 'idle', 'reason': 'No motion in the link.',
                    'timeline': list(self._identity_timeline)}
        self._quiet_since = None
        trained_fs = report.get('fs') or fs
        result = self.identifier.predict(
            extract_features(X[-n_win:], fs),
            min_confidence=cfg.get('min_confidence', 0.55),
            min_margin=cfg.get('min_margin', 0.15),
        )
        if result is None:
            return {**base, 'status': 'untrained'}
        self._push_timeline(result)
        out = {**base, 'status': 'identified' if result['accepted'] else 'unknown',
               **result, 'timeline': list(self._identity_timeline)}
        if abs(trained_fs - fs) > 0.1 * trained_fs:
            out['warning'] = (f'Model was trained at {trained_fs:.0f} Hz but the stream is '
                              f'{fs:.0f} Hz — re-enrol at the current rate.')
        return out

    def _push_timeline(self, result):
        self._identity_timeline.append({
            't': round(time.time(), 1),
            'label': result['label'] if result else None,
            'p': {r['label']: r['p'] for r in result['probabilities']} if result else {},
        })

    def measurements(self, identity_cfg: dict | None = None) -> dict | None:
        """Compact v2 measurements for attaching to a detection record."""
        snap = self.snapshot(max_age=0.9, identity_cfg=identity_cfg)
        if not snap.get('ready'):
            return None
        v, ident = snap['vitals'], snap['identity']
        out = {
            'fs_hz': snap['health']['fs_hz'],
            'motion_state': snap['motion']['state'],
            'harmonic_ratio': v['harmonic_ratio'],
        }
        for key, name in (('respiration', 'respiratory_rate'), ('heart', 'bpm'),
                          ('cadence', 'cadence_spm')):
            out[name] = v[key]['per_min']
            out[f'{name}_valid'] = v[key]['valid']
            out[f'{name}_snr_db'] = v[key]['snr_db']
        if ident.get('status') in ('identified', 'unknown'):
            out['identity'] = {k: ident[k] for k in
                               ('label', 'best_guess', 'accepted', 'confidence', 'enrolled')}
        return out

    # ── Baseline ────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(self._state_path, 'r') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def set_baseline(self) -> dict:
        """Record the current motion level as the empty-room floor."""
        recent = np.array(list(self._motion_history)[-10:])
        if len(recent) < 5:
            raise ValueError('Not enough data yet — wait a few seconds and retry.')
        floor = np.median(recent, axis=0)
        if not np.all(floor > 0):
            raise ValueError('Signal is flat — is CSI arriving?')
        self._baseline_floor = (float(floor[0]), float(floor[1]))
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self._state_path, 'w') as f:
            json.dump({'motion_floor': list(self._baseline_floor), 'captured_at': time.time()}, f)
        return {'motion_floor': list(self._baseline_floor)}

    def clear_baseline(self):
        self._baseline_floor = None
        try:
            os.remove(self._state_path)
        except OSError:
            pass

    # ── Enrollment capture ──────────────────────────────────────────────────

    def start_capture(self, label: str, session: str, path: str,
                      duration_s: float, attrs: dict | None = None) -> dict:
        label, session, path = (label or '').strip(), (session or '').strip(), (path or '').strip()
        for name, value in (('name', label), ('session', session), ('path', path)):
            if not _LABEL_RE.match(value):
                raise ValueError(f'{name} must be 1–40 letters, digits, spaces, . _ or -')
        if label.lower() == 'unknown':
            raise ValueError("'unknown' is reserved")
        if not (FEATURE_WINDOW_S + 1 <= duration_s <= MAX_CAPTURE_SECONDS):
            raise ValueError(f'duration must be {FEATURE_WINDOW_S + 1:.0f}–{MAX_CAPTURE_SECONDS:.0f} s')
        with self._buf_lock:
            if self._capture is not None:
                raise ValueError('A capture is already in progress')
            if not self._frames:
                raise ValueError('No CSI frames are arriving')
            self._capture = {
                'label': label, 'session': session, 'path': path,
                'duration_s': float(duration_s), 'attrs': attrs or {},
                'start_ts': self._last_ts, 'started_wall': time.time(),
            }
        return self.capture_status()

    def capture_status(self) -> dict:
        cap = self._capture
        if cap is None:
            return {'active': False, 'last': self._last_capture}
        elapsed = self._last_ts - cap['start_ts']
        return {'active': True, 'label': cap['label'], 'session': cap['session'],
                'path': cap['path'], 'duration_s': cap['duration_s'],
                'elapsed_s': round(max(0.0, elapsed), 1), 'last': self._last_capture}

    def _advance_capture(self) -> dict:
        cap = self._capture
        if cap is not None and self._last_ts - cap['start_ts'] >= cap['duration_s']:
            self.stop_capture()
        return self.capture_status()

    def stop_capture(self, discard: bool = False) -> dict:
        """Finish the active capture: featurise it, store it, save the raw frames."""
        with self._buf_lock:
            cap, self._capture = self._capture, None
        if cap is None:
            return self.capture_status()
        if discard:
            self._last_capture = {'label': cap['label'], 'saved': False, 'reason': 'discarded'}
            return self.capture_status()

        ts, amps, rssi = self._copy_buffer()
        end_ts = min(self._last_ts, cap['start_ts'] + cap['duration_s'])
        sel = (ts > cap['start_ts']) & (ts <= end_ts)
        outcome = {'label': cap['label'], 'session': cap['session'], 'path': cap['path'],
                   'saved': False}
        if np.count_nonzero(sel) < 64:
            outcome['reason'] = 'Too few frames were captured.'
        else:
            rate = dsp.measure_sample_rate(ts[sel], self.nominal_fs)
            fs = float(np.clip(rate['fs'], 5.0, 2000.0))
            _, X = dsp.resample_uniform(ts[sel], amps[sel], fs)
            X, _ = dsp.hampel(X)
            feats = windows_from_capture(X, fs)
            if not feats:
                outcome['reason'] = 'Capture was shorter than one feature window.'
            else:
                cid = self.enrollment.add_capture(cap['label'], cap['session'], cap['path'],
                                                  fs, feats, cap['attrs'])
                self._save_recording(cid, cap, ts[sel], amps[sel], rssi[sel])
                outcome.update(saved=True, samples=len(feats), fs_hz=round(fs, 1),
                               seconds=round(float(ts[sel][-1] - ts[sel][0]), 1))
        self._last_capture = outcome
        return self.capture_status()

    def _recording_dir(self, label: str) -> str:
        return os.path.join(self.data_dir, 'recordings', re.sub(r'[^\w.\-]+', '_', label))

    def _save_recording(self, capture_id, cap, ts, amps, rssi):
        """Keep the raw frames so features can be re-derived later without
        asking everyone to walk again."""
        try:
            rec_dir = self._recording_dir(cap['label'])
            os.makedirs(rec_dir, exist_ok=True)
            safe = re.sub(r'[^\w.\-]+', '_', capture_id)
            np.savez_compressed(os.path.join(rec_dir, f'{safe}.npz'),
                                timestamps=ts, amplitudes=amps.astype(np.float32), rssi=rssi,
                                label=cap['label'], session=cap['session'], path=cap['path'])
        except OSError:
            pass   # the feature vectors are already stored; raw frames are a bonus

    def train(self) -> dict:
        report = self.identifier.train()
        self._identity_timeline.clear()
        return report

    def delete_person(self, label: str) -> bool:
        """Remove a person's enrolment, including their raw recordings."""
        removed = self.enrollment.delete_label(label)
        if removed:
            shutil.rmtree(self._recording_dir(label), ignore_errors=True)
            self.train()
        return removed
