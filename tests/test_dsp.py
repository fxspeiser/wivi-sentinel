"""
Tests for the v2 DSP primitives.

Run with:  python3 tests/test_dsp.py      (no pytest needed)
       or:  python3 -m pytest tests/ -q
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import dsp

FS = 100.0


def _t(seconds):
    return np.arange(int(seconds * FS)) / FS


def test_respiration_is_not_quantised_to_the_welch_grid():
    """The original 256-sample Welch could only answer in 23.4/min steps."""
    rng = np.random.default_rng(1)
    for true_bpm in (11.0, 15.0, 18.5, 22.0):
        x = np.sin(2 * np.pi * (true_bpm / 60) * _t(30)) + 0.3 * rng.normal(size=3000)
        est = dsp.estimate_rate(x, FS, (0.1, 0.6), 20.0)
        assert abs(est['per_min'] - true_bpm) < 1.0, (true_bpm, est)
        assert est['confidence'] > 0.7


def test_cadence_resolves_a_ten_step_difference():
    """107 vs 117 steps/min — the gap the sex heuristic depends on."""
    rng = np.random.default_rng(2)
    got = []
    for spm in (107.0, 117.0):
        x = np.sin(2 * np.pi * (spm / 60) * _t(10)) + 0.3 * rng.normal(size=1000)
        got.append(dsp.estimate_rate(x, FS, (0.8, 4.0), 8.0)['per_min'])
    assert abs(got[0] - 107) < 2 and abs(got[1] - 117) < 2, got


def test_noise_gets_low_confidence():
    rng = np.random.default_rng(3)
    confs = [dsp.estimate_rate(rng.normal(size=3000), FS, (0.1, 0.6), 20.0)['confidence']
             for _ in range(20)]
    assert np.median(confs) < 0.2 and max(confs) < 0.5, confs


def test_short_window_is_refused_not_guessed():
    x = np.sin(2 * np.pi * 0.2 * _t(4))          # under 1.5 cycles of the band floor
    est = dsp.estimate_rate(x, FS, (0.1, 0.6), 20.0)
    assert est['per_min'] == 0.0 and est['confidence'] == 0.0


def test_harmonic_ratio_survives_above_two_hertz():
    """A 2.4 Hz cadence has its 2nd harmonic at 4.8 Hz — outside the old 0.8–4 Hz
    band-pass, which is why the original chain read ~0 and called humans dogs."""
    f0 = 2.4
    x = np.sin(2 * np.pi * f0 * _t(10)) + 0.7 * np.sin(2 * np.pi * 2 * f0 * _t(10))
    assert abs(dsp.harmonic_ratio(x, FS, f0) - 0.49) < 0.05
    assert dsp.harmonic_ratio(x, FS, 30.0) == 0.0        # 2*f0 above Nyquist


def test_sample_rate_is_measured_from_timestamps():
    rng = np.random.default_rng(4)
    ts = np.cumsum(np.full(2000, 1 / 62.0) + rng.normal(0, 0.0005, 2000))
    rate = dsp.measure_sample_rate(ts, nominal_fs=100.0)
    assert rate['valid'] and abs(rate['fs'] - 62.0) < 1.0
    assert 0.2 < rate['jitter_ms'] < 1.5


def test_dropped_frames_are_reported():
    ts = np.arange(1000) / FS
    ts = np.delete(ts, np.arange(400, 450))             # one 50-frame hole
    rate = dsp.measure_sample_rate(ts)
    assert rate['gaps'] == 1 and 4.0 < rate['loss_pct'] < 6.0


def test_unusable_timestamps_fall_back_to_nominal():
    rate = dsp.measure_sample_rate(np.zeros(100), nominal_fs=100.0)
    assert rate['valid'] is False and rate['fs'] == 100.0


def test_resampling_corrects_an_assumed_rate_error():
    """Frames really arriving at 50 Hz must not be read as if they were 100 Hz."""
    real_fs = 50.0
    ts = np.arange(1500) / real_fs
    x = np.sin(2 * np.pi * 1.5 * ts)[:, None]
    fs = dsp.measure_sample_rate(ts)['fs']
    _, xu = dsp.resample_uniform(ts, x, fs)
    est = dsp.estimate_rate(xu[:, 0], fs, (0.8, 4.0), 8.0)
    assert abs(est['hz'] - 1.5) < 0.03, est


def test_resample_drops_backwards_timestamps():
    ts = np.array([0.0, 0.01, 0.02, 0.015, 0.03, 0.04])
    grid, xu = dsp.resample_uniform(ts, np.arange(6.0)[:, None], 100.0)
    assert np.all(np.diff(grid) > 0) and xu.shape[0] == grid.size


def test_hampel_removes_spikes_and_leaves_signal():
    rng = np.random.default_rng(5)
    x = np.sin(2 * np.pi * 1.0 * _t(10))[:, None] + 0.01 * rng.normal(size=(1000, 1))
    dirty = x.copy()
    dirty[[100, 500, 900], 0] += 8.0
    clean, n = dsp.hampel(dirty)
    assert n >= 3 and np.max(np.abs(clean - x)) < 0.2
    _, n_gauss = dsp.hampel(rng.normal(size=(5000, 4)))
    assert n_gauss / 20000 < 0.02                       # barely touches plain noise


def test_pca_recovers_the_shared_waveform():
    rng = np.random.default_rng(6)
    wave = np.sin(2 * np.pi * 1.3 * _t(10))
    X = wave[:, None] * rng.random(52)[None, :] + 0.05 * rng.normal(size=(1000, 52))
    scores, var, _ = dsp.pca(X, k=3)
    assert var[0] > 0.8
    assert abs(np.corrcoef(scores[:, 0], wave)[0, 1]) > 0.98


def test_spectrogram_puts_energy_at_the_right_frequency():
    x = np.sin(2 * np.pi * 20.0 * _t(10))
    f, t, s = dsp.motion_spectrogram(x, FS)
    assert s.shape == (f.size, t.size) and f[-1] == FS / 2
    assert abs(f[np.argmax(s.mean(axis=1))] - 20.0) < 2.0


def test_motion_gate_separates_quiet_still_and_moving():
    import synth

    def ratios(x, floor_x):
        fast = dsp.motion_index(x[-200:]) / dsp.motion_index(floor_x[-200:])
        slow = dsp.presence_index(x, FS) / dsp.presence_index(floor_x, FS)
        return fast, slow

    floor = synth.empty(10, seed=1)
    assert dsp.classify_motion(*ratios(synth.empty(10, seed=2), floor)) == 'quiet'
    assert dsp.classify_motion(*ratios(synth.still(10), floor)) == 'still'
    assert dsp.classify_motion(*ratios(synth.walk(synth.person(0), 10), floor)) == 'moving'


if __name__ == '__main__':
    from _runner import run
    run(globals())
