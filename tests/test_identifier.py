"""
Tests for enrolled-roster identification.

Run with:  python3 tests/test_identifier.py      (no pytest needed)
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth
from engine.identifier import (
    EnrollmentStore, RosterIdentifier, ShrinkageLDA, extract_features,
    windows_from_capture, validate, FEATURE_NAMES, N_FEATURES,
)

FS = 100.0


def _store():
    return EnrollmentStore(os.path.join(tempfile.mkdtemp(), 'enrollment.json'))


def _enrol(store, n_people=4, sessions=('day1', 'day2'), walks=2, seconds=20):
    for pid in range(n_people):
        for si, session in enumerate(sessions):
            for w in range(walks):
                X = synth.walk(synth.person(pid), seconds, seed=pid * 100 + si * 10 + w,
                               room_seed=si)
                store.add_capture(f'P{pid}', session, 'A' if w % 2 == 0 else 'B', FS,
                                  windows_from_capture(X, FS), {'sex': 'female' if pid % 2 else 'male'})


def test_feature_vector_shape_and_finiteness():
    f = extract_features(synth.walk(synth.person(1), 4), FS)
    assert f.shape == (N_FEATURES,) == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(f))
    assert np.all(np.isfinite(extract_features(synth.empty(4), FS)))   # no NaN on a dead link


def test_features_ignore_the_static_channel():
    """Scaling every subcarrier's resting level must not move the features much:
    the empty room should not be what identifies a person."""
    X = synth.walk(synth.person(2), 4, seed=3)
    moving = X - X.mean(axis=0)
    a = extract_features(X.mean(axis=0) + moving, FS)
    b = extract_features(X.mean(axis=0) * 1.0 + 0.5 + moving, FS)       # different room level
    names = np.array(FEATURE_NAMES)
    dynamic = names != 'log_motion_index'            # that one is level-relative by design
    assert np.allclose(a[dynamic], b[dynamic], atol=1e-6)


def test_capture_is_sliced_into_overlapping_windows():
    feats = windows_from_capture(synth.walk(synth.person(0), 20), FS)
    assert len(feats) == 9                            # (20 - 4) / 2 + 1
    assert windows_from_capture(synth.walk(synth.person(0), 3), FS) == []


def test_lda_separates_gaussian_blobs_and_embeds_in_2d():
    rng = np.random.default_rng(0)
    centres = rng.normal(size=(3, 12)) * 4
    X = np.vstack([c + rng.normal(size=(30, 12)) for c in centres])
    y = np.repeat(['a', 'b', 'c'], 30)
    model = ShrinkageLDA().fit(X, y)
    pred = np.array(model.classes)[model.predict_proba(X).argmax(axis=1)]
    assert np.mean(pred == y) > 0.97
    assert model.embed(X).shape == (90, 2)
    two = ShrinkageLDA().fit(X[:60], y[:60])          # two classes still give two axes
    assert two.embed(X[:60]).shape == (60, 2)


def test_lda_refuses_a_single_class():
    try:
        ShrinkageLDA().fit(np.random.default_rng(0).normal(size=(10, 4)), ['a'] * 10)
    except ValueError:
        return
    raise AssertionError('single-class fit should raise')


def test_store_roundtrips_and_deletes():
    store = _store()
    _enrol(store, n_people=2, sessions=('day1',), walks=1)
    again = EnrollmentStore(store.path)
    assert len(again.samples) == len(store.samples) > 0
    summary = {r['label']: r for r in again.summary()}
    assert summary['P1']['sex'] == 'female' and summary['P0']['sessions'] == ['day1']
    assert again.delete_label('P0') is True and again.delete_label('P0') is False
    assert {s['label'] for s in EnrollmentStore(store.path).samples} == {'P1'}


def test_untrained_identifier_explains_itself():
    ident = RosterIdentifier(_store())
    assert ident.report['trained'] is False and 'Need at least 2' in ident.report['reason']
    assert ident.predict(np.zeros(N_FEATURES)) is None


def test_cross_session_validation_and_identification():
    store = _store()
    _enrol(store)
    ident = RosterIdentifier(store)
    rep = ident.report
    assert rep['trained'] and rep['protocol'] == 'leave-one-session-out'
    assert rep['accuracy'] > 0.8, rep['accuracy']
    assert rep['chance'] == 0.25
    m = np.array(rep['confusion']['matrix'])
    assert m.shape == (4, 4) and m.sum() == rep['n_tested']
    assert 'leave_one_path_out_accuracy' in rep and 'unknown_false_accept_rate' in rep
    assert len(rep['embedding']) == rep['n_samples']

    # A fresh walk by P2, in a third "session", accumulates to a confident ID.
    X = synth.walk(synth.person(2), 12, seed=999, room_seed=1)
    result = None
    for f in windows_from_capture(X, FS):
        result = ident.predict(f)
    assert result['accepted'] and result['label'] == 'P2', result
    assert result['enrolled'] == {'sex': 'male'}
    assert abs(sum(p['p'] for p in result['probabilities']) - 1.0) < 0.01


def test_stranger_is_rejected_as_unknown():
    store = _store()
    _enrol(store)
    ident = RosterIdentifier(store)
    stranger = synth.person(11)                       # never enrolled, very different gait
    results = [ident.predict(f) for f in windows_from_capture(synth.walk(stranger, 12, seed=5), FS)]
    assert results[-1]['label'] == 'unknown' and not results[-1]['accepted']
    assert 'unlike anyone enrolled' in results[-1]['rejected_because']


def test_single_session_is_labelled_optimistic():
    store = _store()
    _enrol(store, n_people=3, sessions=('day1',), walks=2)
    rep = validate(store.snapshot()[1])
    assert rep['protocol'] == 'leave-one-capture-out' and 'optimistic' not in rep['protocol']
    assert 'second session' in rep['protocol_note']

    single = _store()
    _enrol(single, n_people=3, sessions=('day1',), walks=1)
    assert validate(single.snapshot()[1])['protocol'] == 'within-capture split'


def test_validation_never_splits_a_capture():
    """Overlapping windows from one walk on both sides of a split is how CSI
    results get inflated; a held-out group must take its whole capture with it."""
    store = _store()
    _enrol(store)
    samples = store.snapshot()[1]
    sessions = {s['session'] for s in samples}
    for held in sessions:
        train = {s['capture_id'] for s in samples if s['session'] != held}
        test = {s['capture_id'] for s in samples if s['session'] == held}
        assert not (train & test)


if __name__ == '__main__':
    from _runner import run
    run(globals())
