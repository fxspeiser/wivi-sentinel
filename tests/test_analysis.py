"""
Tests for the v2 analysis engine (buffering, gating, payloads, enrolment).

Run with:  python3 tests/test_analysis.py      (no pytest needed)
"""

import base64
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth
from engine.analysis import AnalysisEngine

FS = 100.0


def _engine():
    return AnalysisEngine(tempfile.mkdtemp(), nominal_fs=FS)


def _feed(engine, matrix, t0=0.0, chunk=100, **kw):
    """Feed in 1 s chunks, snapshotting as the server would."""
    fr = synth.frames(matrix, FS, t0=t0, **kw)
    snap = None
    for i in range(0, len(fr), chunk):
        engine.ingest(fr[i:i + chunk])
        snap = engine.snapshot(max_age=0)
    return snap, t0 + len(fr) / FS


def test_not_ready_until_frames_arrive():
    snap = _engine().snapshot(max_age=0)
    assert snap['ready'] is False and snap['capture']['active'] is False


def test_overlapping_windows_are_deduplicated():
    eng = _engine()
    fr = synth.frames(synth.empty(5), FS)
    assert eng.ingest(fr[:300]) == 300
    assert eng.ingest(fr[100:400]) == 100             # 200 already seen
    assert eng.ingest(fr[:400]) == 0


def test_device_timestamp_and_raw_amplitudes_are_preferred():
    eng = _engine()
    fr = synth.frames(synth.empty(3), FS)
    for i, f in enumerate(fr):
        f.timestamp = 1000.0                           # useless host clock
        f.device_ts = i / 50.0                         # real rate: 50 Hz
        f.raw_amplitudes = np.ones(40)
    eng.ingest(fr)
    snap = eng.snapshot(max_age=0)
    assert abs(snap['health']['fs_hz'] - 50.0) < 1.0
    assert snap['health']['subcarriers'] == 40


def test_still_subject_yields_respiration_but_gates_cadence():
    eng = _engine()
    _, t = _feed(eng, synth.empty(15, seed=1))
    eng.set_baseline()
    snap, _ = _feed(eng, synth.still(40, resp_hz=0.3, seed=2), t0=t)
    assert snap['motion']['state'] == 'still', snap['motion']
    resp = snap['vitals']['respiration']
    assert resp['valid'] and abs(resp['per_min'] - 18.0) < 1.0, resp
    cad = snap['vitals']['cadence']
    assert cad['valid'] is False and cad['gated'] is True
    assert snap['vitals']['heart']['experimental'] is True


def test_walking_yields_cadence_but_gates_vitals():
    eng = _engine()
    _, t = _feed(eng, synth.empty(15, seed=1))
    eng.set_baseline()
    p = synth.person(1)
    snap, _ = _feed(eng, synth.walk(p, 20, seed=3), t0=t)
    assert snap['motion']['state'] == 'moving'
    cad = snap['vitals']['cadence']
    assert cad['valid'] and abs(cad['hz'] - p['cadence_hz']) < 0.1, cad
    assert snap['vitals']['respiration']['gated'] is True
    assert abs(snap['vitals']['harmonic_ratio'] - p['harmonic'] ** 2) < 0.1


def test_empty_room_is_quiet_and_reports_nothing_as_valid():
    eng = _engine()
    _, t = _feed(eng, synth.empty(15, seed=1))
    eng.set_baseline()
    snap, _ = _feed(eng, synth.empty(20, seed=4), t0=t)
    assert snap['motion']['state'] == 'quiet'
    assert not any(snap['vitals'][k]['valid'] for k in ('respiration', 'heart', 'cadence'))


def test_baseline_persists_and_clears():
    eng = _engine()
    _feed(eng, synth.empty(15))
    eng.set_baseline()
    again = AnalysisEngine(eng.data_dir, nominal_fs=FS)
    assert again._baseline_floor == eng._baseline_floor
    again.clear_baseline()
    assert AnalysisEngine(eng.data_dir, nominal_fs=FS)._baseline_floor is None


def test_image_payloads_decode_to_their_stated_shape():
    eng = _engine()
    snap, _ = _feed(eng, synth.walk(synth.person(0), 20))
    for key in ('spectrogram', 'heatmap'):
        img = snap[key]
        raw = base64.b64decode(img['data'])
        assert len(raw) == img['rows'] * img['cols'] > 0, key
        assert img['cols'] <= 240
    assert snap['heatmap']['rows'] == synth.N_SUB
    assert snap['spectrogram']['f_max_hz'] == FS / 2
    psd = snap['psd']
    assert len(psd['freqs']) == len(psd['db']) == 200 and max(psd['db']) == 0.0
    assert psd['resolution_hz'] < psd['legacy_resolution_hz']


def test_measurements_are_compact_and_flagged():
    eng = _engine()
    assert eng.measurements() is None
    _feed(eng, synth.walk(synth.person(0), 12))
    m = eng.measurements()
    for key in ('bpm', 'respiratory_rate', 'cadence_spm'):
        assert key in m and f'{key}_valid' in m and f'{key}_snr_db' in m
    assert 'identity' not in m                         # nobody enrolled


def test_capture_validation_rejects_bad_input():
    eng = _engine()
    _feed(eng, synth.empty(2))
    for bad in (dict(label='', session='d1', path='A', duration_s=10),
                dict(label='unknown', session='d1', path='A', duration_s=10),
                dict(label='../x', session='d1', path='A', duration_s=10),
                dict(label='Ann', session='d1', path='A', duration_s=2),
                dict(label='Ann', session='d1', path='A', duration_s=600)):
        try:
            eng.start_capture(**bad)
        except ValueError:
            continue
        raise AssertionError(f'accepted {bad}')
    eng.start_capture('Ann', 'd1', 'A', 10)
    try:
        eng.start_capture('Bob', 'd1', 'A', 10)
    except ValueError:
        return
    raise AssertionError('second concurrent capture accepted')


def test_enrol_train_identify_end_to_end():
    eng = _engine()
    snap, t = _feed(eng, synth.empty(15, seed=1))
    eng.set_baseline()
    assert snap['identity']['status'] == 'untrained'

    for pid in range(3):
        for si, session in enumerate(('day1', 'day2')):
            eng.start_capture(f'P{pid}', session, 'A', 16, {'sex': 'female' if pid == 1 else 'male'})
            snap, t = _feed(eng, synth.walk(synth.person(pid), 17, seed=pid * 10 + si,
                                            room_seed=si), t0=t)
            last = snap['capture']['last']
            assert snap['capture']['active'] is False and last['saved'], last
            assert last['samples'] >= 6

    rec_dir = os.path.join(eng.data_dir, 'recordings', 'P0')
    assert len(os.listdir(rec_dir)) == 2               # raw frames kept per capture

    report = eng.train()
    assert report['trained'] and report['protocol'] == 'leave-one-session-out'
    assert report['accuracy'] > 0.8, report['accuracy']

    snap, t = _feed(eng, synth.walk(synth.person(1), 12, seed=77, room_seed=1), t0=t)
    ident = snap['identity']
    assert ident['status'] == 'identified' and ident['label'] == 'P1', ident
    assert ident['enrolled']['sex'] == 'female'
    assert ident['timeline'][-1]['label'] == 'P1'
    assert eng.measurements()['identity']['label'] == 'P1'

    snap, t = _feed(eng, synth.empty(14, seed=9), t0=t)   # room empties (presence window is 10 s)
    assert snap['identity']['status'] == 'idle'

    assert eng.delete_person('P0') is True
    assert not os.path.exists(rec_dir)
    assert eng.identifier.report['classes'] == ['P1', 'P2']
    assert eng.delete_person('nobody') is False


def test_discarded_capture_stores_nothing():
    eng = _engine()
    _, t = _feed(eng, synth.empty(3))
    eng.start_capture('Ann', 'd1', 'A', 10)
    _feed(eng, synth.walk(synth.person(0), 4), t0=t)
    status = eng.stop_capture(discard=True)
    assert status['active'] is False and status['last']['saved'] is False
    assert eng.enrollment.samples == []


if __name__ == '__main__':
    from _runner import run
    run(globals())
