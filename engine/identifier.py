"""
Enrolled-roster identification (closed-set, supervised)
=======================================================
Identifies *which enrolled person* is walking, then reads their enrolled
attributes (sex, species). This runs beside the unsupervised
`SignatureMatcher` in `csi_processor.py`; it does not replace it.

Why a second path: every subject in the deployment is enrolled and labelled,
so identity is a supervised problem over a known roster. A classifier trained
on labelled walks can separate people the population-prior heuristics cannot.

Pieces:
  extract_features  — ~31 motion/gait features from a short walking window
  ShrinkageLDA      — regularised linear discriminant classifier (numpy only,
                      sub-millisecond inference on a Pi 4)
  EnrollmentStore   — labelled feature samples + roster attributes on disk
  RosterIdentifier  — train / validate / predict with open-set rejection

The confound that matters: CSI encodes the *room and the path* as much as the
person, so a classifier can score highly by learning "lane 2, Tuesday".
`validate()` therefore holds out whole sessions (and whole paths) rather than
random windows, and says so plainly when the data only supports a weaker,
optimistic split.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict

import numpy as np

from engine import dsp

EPS = 1e-12

FEATURE_WINDOW_S = 4.0      # seconds of walking per feature vector
FEATURE_HOP_S = 2.0         # hop between vectors inside one capture
_BAND_EDGES_HZ = (0.3, 1.0, 2.0, 4.0, 8.0, 15.0, 25.0, 50.0)
_SHAPE_BINS = 8

FEATURE_NAMES = (
    [f'pc{i + 1}_var' for i in range(4)]
    + [f'band_{lo:g}_{hi:g}hz' for lo, hi in zip(_BAND_EDGES_HZ[:-1], _BAND_EDGES_HZ[1:])]
    + ['centroid_hz', 'spread_hz', 'entropy', 'flatness', 'rolloff95_hz']
    + ['cadence_hz', 'harmonic_ratio', 'stride_regularity']
    + ['pc1_skew', 'pc1_kurtosis', 'pc1_zero_cross_hz']
    + ['log_motion_index']
    + [f'shape_{i}' for i in range(_SHAPE_BINS)]
)
N_FEATURES = len(FEATURE_NAMES)


# ─── Features ────────────────────────────────────────────────────────────────

def extract_features(window: np.ndarray, fs: float) -> np.ndarray:
    """Feature vector for one (n, m) uniformly-sampled, denoised amplitude window.

    Only *dynamic* quantities are used: everything is computed after removing
    each subcarrier's mean, so the static channel (the empty room) contributes
    nothing directly. The subcarrier 'shape' features still depend on where in
    the room the motion happened, which is why validation holds out paths.
    """
    X = np.asarray(window, dtype=np.float64)
    hp = dsp.highpass(X, fs, 0.3)
    scores, var_ratio, _ = dsp.pca(hp, k=4)
    var4 = np.zeros(4)
    var4[:var_ratio.size] = var_ratio
    pc1 = scores[:, 0]

    # Spectrum of the top-3 components, summed.
    power = None
    for j in range(min(3, scores.shape[1])):
        freqs, p = dsp.periodogram(scores[:, j], fs, pad_factor=2)
        power = p if power is None else power + p
    nyq = fs / 2.0
    usable = (freqs >= _BAND_EDGES_HZ[0]) & (freqs <= nyq)
    f_u, p_u = freqs[usable], power[usable]
    total = float(np.sum(p_u)) + EPS
    pn = p_u / total

    bands = []
    for lo, hi in zip(_BAND_EDGES_HZ[:-1], _BAND_EDGES_HZ[1:]):
        m = (f_u >= lo) & (f_u < hi)
        bands.append(np.log10(float(np.sum(pn[m])) + 1e-6))

    centroid = float(np.sum(f_u * pn))
    spread = float(np.sqrt(np.sum(((f_u - centroid) ** 2) * pn)))
    entropy = float(-np.sum(pn * np.log(pn + EPS)) / np.log(max(pn.size, 2)))
    flatness = float(np.exp(np.mean(np.log(p_u + EPS))) / (np.mean(p_u) + EPS))
    cum = np.cumsum(pn)
    rolloff = float(f_u[min(np.searchsorted(cum, 0.95), f_u.size - 1)])

    cadence = dsp.estimate_rate(pc1, fs, (0.8, 4.0), FEATURE_WINDOW_S)
    f0 = cadence['hz']
    h_ratio = dsp.harmonic_ratio(pc1, fs, f0) if f0 > 0 else 0.0
    stride_reg = _autocorr_at(pc1, fs, f0) if f0 > 0 else 0.0

    z = (pc1 - pc1.mean()) / (pc1.std() + EPS)
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4) - 3.0)
    zero_cross = float(np.count_nonzero(np.diff(np.signbit(z))) / (len(z) / fs) / 2.0)

    log_motion = float(np.log10(dsp.motion_index(X) + 1e-9))

    per_sub = hp.std(axis=0)
    per_sub = per_sub / (np.sum(per_sub) + EPS)
    shape = np.array([chunk.sum() for chunk in np.array_split(per_sub, _SHAPE_BINS)])

    return np.concatenate([
        var4, bands, [centroid, spread, entropy, flatness, rolloff],
        [f0, min(h_ratio, 10.0), stride_reg], [skew, kurt, zero_cross],
        [log_motion], shape,
    ]).astype(np.float64)


def _autocorr_at(x: np.ndarray, fs: float, f0: float) -> float:
    """Normalised autocorrelation of x at one period of f0 (stride regularity)."""
    lag = int(round(fs / f0))
    x = x - x.mean()
    if lag <= 0 or lag >= x.size - 1:
        return 0.0
    denom = float(np.dot(x, x)) + EPS
    return float(np.dot(x[:-lag], x[lag:]) / denom)


def windows_from_capture(matrix: np.ndarray, fs: float) -> list:
    """Slice one continuous capture into overlapping feature vectors."""
    n_win = int(round(FEATURE_WINDOW_S * fs))
    hop = max(1, int(round(FEATURE_HOP_S * fs)))
    feats = []
    for start in range(0, matrix.shape[0] - n_win + 1, hop):
        f = extract_features(matrix[start:start + n_win], fs)
        if np.all(np.isfinite(f)):
            feats.append(f)
    return feats


# ─── Classifier ──────────────────────────────────────────────────────────────

class ShrinkageLDA:
    """Linear discriminant analysis with a shrunk pooled covariance.

    With tens of samples and ~31 features the pooled covariance is poorly
    conditioned; shrinking it toward a scaled identity keeps the inverse
    stable. Scores are Mahalanobis-based, so the same model gives class
    posteriors, a distance for open-set rejection and a 2-D embedding.
    """

    def __init__(self, shrinkage: float = 0.3):
        self.shrinkage = float(shrinkage)
        self.classes: list = []
        self.temperature = 1.0

    def fit(self, X: np.ndarray, y) -> 'ShrinkageLDA':
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y)
        self.classes = sorted(set(y.tolist()))
        if len(self.classes) < 2:
            raise ValueError('need at least two enrolled people to train')
        self.mu_ = X.mean(axis=0)
        self.sd_ = X.std(axis=0) + 1e-6
        Z = (X - self.mu_) / self.sd_
        d = Z.shape[1]
        self.means_ = np.vstack([Z[y == c].mean(axis=0) for c in self.classes])
        resid = np.vstack([Z[y == c] - self.means_[i] for i, c in enumerate(self.classes)])
        dof = max(1, Z.shape[0] - len(self.classes))
        sw = resid.T @ resid / dof
        g = self.shrinkage
        cov = (1 - g) * sw + g * (np.trace(sw) / d + EPS) * np.eye(d)
        evals, evecs = np.linalg.eigh(cov)
        evals = np.maximum(evals, 1e-8)
        self.whiten_ = evecs @ np.diag(evals ** -0.5) @ evecs.T   # Σ^(-1/2)
        self.wmeans_ = self.means_ @ self.whiten_

        # 2-D embedding: discriminant axes (PCA of whitened class means), padded
        # with the top residual direction when there are only two classes.
        centred = self.wmeans_ - self.wmeans_.mean(axis=0)
        _, s, vt = np.linalg.svd(centred, full_matrices=False)
        axes = [vt[i] for i in range(min(2, int(np.sum(s > 1e-9))))]
        if len(axes) < 2:
            W = Z @ self.whiten_
            W = W - W.mean(axis=0)
            for a in axes:
                W = W - np.outer(W @ a, a)
            _, _, vt2 = np.linalg.svd(W, full_matrices=False)
            axes.append(vt2[0])
        self.embed_axes_ = np.vstack(axes[:2])
        self.embed_origin_ = self.wmeans_.mean(axis=0)
        return self

    def _whitened(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        return ((X - self.mu_) / self.sd_) @ self.whiten_

    def distances(self, X: np.ndarray) -> np.ndarray:
        """Squared Mahalanobis distance from each row to each class mean."""
        W = self._whitened(X)
        diff = W[:, None, :] - self.wmeans_[None, :, :]
        return np.sum(diff ** 2, axis=2)

    def log_scores(self, X: np.ndarray) -> np.ndarray:
        """Per-class discriminant scores (uniform priors), temperature-scaled."""
        return -0.5 * self.distances(X) / self.temperature

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _softmax(self.log_scores(X))

    def embed(self, X: np.ndarray) -> np.ndarray:
        return (self._whitened(X) - self.embed_origin_) @ self.embed_axes_.T


def _softmax(scores: np.ndarray) -> np.ndarray:
    s = scores - scores.max(axis=1, keepdims=True)
    e = np.exp(s)
    return e / e.sum(axis=1, keepdims=True)


def _fit_temperature(raw_scores: np.ndarray, y_idx: np.ndarray) -> float:
    """Single-parameter calibration: the temperature minimising held-out NLL."""
    best_t, best_nll = 1.0, np.inf
    for t in np.logspace(0, 2.5, 40):
        p = _softmax(raw_scores / t)
        nll = -np.mean(np.log(p[np.arange(len(y_idx)), y_idx] + EPS))
        if nll < best_nll:
            best_t, best_nll = float(t), float(nll)
    return best_t


# ─── Enrollment storage ──────────────────────────────────────────────────────

class EnrollmentStore:
    """Labelled feature samples and roster attributes, persisted as JSON."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self.roster: dict = {}
        self.samples: list = []
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self.roster = data.get('roster', {})
        self.samples = [s for s in data.get('samples', [])
                        if len(s.get('features', [])) == N_FEATURES]

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f'{self.path}.tmp'
        with open(tmp, 'w') as f:
            json.dump({'version': 1, 'feature_names': FEATURE_NAMES,
                       'roster': self.roster, 'samples': self.samples}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def add_capture(self, label: str, session: str, path: str, fs: float,
                    features: list, attrs: dict | None = None) -> str:
        """Store the feature vectors of one continuous labelled capture."""
        capture_id = f'{label}:{session}:{path}:{int(time.time() * 1000)}'
        with self._lock:
            entry = self.roster.setdefault(label, {'created': time.time()})
            for k, v in (attrs or {}).items():
                if v not in (None, ''):
                    entry[k] = v
            for i, f in enumerate(features):
                self.samples.append({
                    'label': label, 'session': session, 'path': path,
                    'capture_id': capture_id, 'index': i, 'fs': round(float(fs), 2),
                    'features': [round(float(v), 6) for v in f],
                })
            self._save()
        return capture_id

    def delete_label(self, label: str) -> bool:
        with self._lock:
            existed = label in self.roster or any(s['label'] == label for s in self.samples)
            self.roster.pop(label, None)
            self.samples = [s for s in self.samples if s['label'] != label]
            if existed:
                self._save()
            return existed

    def snapshot(self) -> tuple:
        with self._lock:
            return dict(self.roster), list(self.samples)

    def summary(self) -> list:
        roster, samples = self.snapshot()
        by_label = defaultdict(list)
        for s in samples:
            by_label[s['label']].append(s)
        out = []
        for label in sorted(set(roster) | set(by_label)):
            rows = by_label.get(label, [])
            out.append({
                'label': label,
                'samples': len(rows),
                'captures': len({r['capture_id'] for r in rows}),
                'sessions': sorted({r['session'] for r in rows}),
                'paths': sorted({r['path'] for r in rows}),
                **{k: v for k, v in roster.get(label, {}).items() if k != 'created'},
            })
        return out


# ─── Identifier ──────────────────────────────────────────────────────────────

class RosterIdentifier:
    """Trains on the enrollment store and identifies live windows."""

    MIN_SAMPLES_PER_PERSON = 4

    def __init__(self, store: EnrollmentStore):
        self.store = store
        self._lock = threading.RLock()
        self.model: ShrinkageLDA | None = None
        self.d2_threshold = np.inf
        self.report: dict = {'trained': False, 'reason': 'No model trained yet.'}
        self._log_post: np.ndarray | None = None
        self.train()

    # ── Training ────────────────────────────────────────────────────────────

    def _dataset(self):
        roster, samples = self.store.snapshot()
        counts = defaultdict(int)
        for s in samples:
            counts[s['label']] += 1
        keep = [s for s in samples if counts[s['label']] >= self.MIN_SAMPLES_PER_PERSON]
        return roster, keep

    def train(self) -> dict:
        """(Re)fit on everything enrolled, then cross-validate. Returns the report."""
        roster, samples = self._dataset()
        labels = sorted({s['label'] for s in samples})
        with self._lock:
            self._log_post = None
            if len(labels) < 2:
                self.model = None
                self.report = {
                    'trained': False,
                    'reason': (f'Need at least 2 people with {self.MIN_SAMPLES_PER_PERSON}+ '
                               f'samples each; have {len(labels)}.'),
                }
                return self.report

            X = np.array([s['features'] for s in samples])
            y = np.array([s['label'] for s in samples])
            report = validate(samples)
            model = ShrinkageLDA().fit(X, y)
            model.temperature = report.get('temperature', 1.0)

            # Open-set threshold: how far held-out samples of *enrolled* people
            # sit from their own class. Anything much further is "unknown".
            own_d2 = report.pop('_own_d2', [])
            if len(own_d2) >= 8:
                self.d2_threshold = float(np.percentile(own_d2, 97.5) * 1.25)
            else:
                train_d2 = model.distances(X)[np.arange(len(y)),
                                              [model.classes.index(c) for c in y]]
                self.d2_threshold = float(np.max(train_d2) * 2.0)

            emb = model.embed(X)
            report.update({
                'trained': True,
                'classes': model.classes,
                'n_samples': int(len(y)),
                'n_features': N_FEATURES,
                'fs': float(np.median([s['fs'] for s in samples])),
                'd2_threshold': round(self.d2_threshold, 2),
                'embedding': [
                    {'label': s['label'], 'session': s['session'],
                     'x': round(float(e[0]), 3), 'y': round(float(e[1]), 3)}
                    for s, e in zip(samples, emb)
                ],
                'roster': {k: {a: b for a, b in v.items() if a != 'created'}
                           for k, v in roster.items()},
            })
            self.model = model
            self.report = report
            return report

    # ── Inference ───────────────────────────────────────────────────────────

    def reset_track(self):
        """Forget accumulated evidence (call when the room goes quiet)."""
        with self._lock:
            self._log_post = None

    def predict(self, features: np.ndarray, min_confidence: float = 0.55,
                min_margin: float = 0.15) -> dict | None:
        """Identify one feature vector, accumulating evidence across calls.

        Per-window log-scores are summed with exponential forgetting, so the
        posterior sharpens over a walk instead of flickering per window.
        Returns None when no model is trained.
        """
        with self._lock:
            model = self.model
            if model is None or not np.all(np.isfinite(features)):
                return None
            scores = model.log_scores(features)[0]
            self._log_post = scores if self._log_post is None else 0.6 * self._log_post + scores
            track = _softmax(self._log_post[None, :])[0]
            window = _softmax(scores[None, :])[0]
            d2 = float(np.min(model.distances(features)[0]))
            order = np.argsort(track)[::-1]
            top, runner = track[order[0]], (track[order[1]] if len(order) > 1 else 0.0)

            reasons = []
            if d2 > self.d2_threshold:
                reasons.append('unlike anyone enrolled')
            if top < min_confidence:
                reasons.append('low confidence')
            if top - runner < min_margin:
                reasons.append('ambiguous between two people')
            label = model.classes[order[0]]
            accepted = not reasons
            roster, _ = self.store.snapshot()
            emb = model.embed(features)[0]
            return {
                'label': label if accepted else 'unknown',
                'best_guess': label,
                'accepted': accepted,
                'confidence': round(float(top), 3),
                'margin': round(float(top - runner), 3),
                'distance': round(d2, 2),
                'distance_limit': round(self.d2_threshold, 2),
                'rejected_because': reasons,
                'probabilities': [
                    {'label': model.classes[i], 'p': round(float(track[i]), 3),
                     'p_window': round(float(window[i]), 3)}
                    for i in order[:6]
                ],
                'enrolled': ({k: v for k, v in roster.get(label, {}).items() if k != 'created'}
                             if accepted else {}),
                'embedding': [round(float(emb[0]), 3), round(float(emb[1]), 3)],
            }


# ─── Validation ──────────────────────────────────────────────────────────────

def validate(samples: list) -> dict:
    """Cross-validate with the strictest split the data supports.

    Order of preference:
      1. leave-one-session-out  — the honest number (cross-day generalisation)
      2. leave-one-capture-out  — same session, different walk (optimistic)
      3. within-capture split   — first 70% vs last 30% of each walk (very optimistic)
    Whole captures are always kept on one side of a split, because overlapping
    windows from one walk leaking across it is the classic way to fake 95%.
    """
    X = np.array([s['features'] for s in samples])
    y = np.array([s['label'] for s in samples])
    classes = sorted(set(y.tolist()))
    sessions = np.array([s['session'] for s in samples])
    captures = np.array([s['capture_id'] for s in samples])
    paths = np.array([s['path'] for s in samples])

    report: dict = {}
    if len(set(sessions.tolist())) >= 2:
        main = _grouped_cv(X, y, sessions, classes)
        report['protocol'] = 'leave-one-session-out'
        report['protocol_note'] = 'Each session was held out in turn. This is the honest figure.'
    elif all(len(set(captures[y == c].tolist())) >= 2 for c in classes):
        main = _grouped_cv(X, y, captures, classes)
        report['protocol'] = 'leave-one-capture-out'
        report['protocol_note'] = ('Only one session is enrolled, so walks were held out '
                                   'within it. Same-session scores run 15–30 points high — '
                                   'enrol a second session for a real figure.')
    else:
        main = _within_capture_cv(X, y, samples, classes)
        report['protocol'] = 'within-capture split'
        report['protocol_note'] = ('Some people have a single recorded walk, so each walk was '
                                   'split 70/30 in time. This mostly measures memorisation — '
                                   'record more walks and a second session.')

    report.update({k: v for k, v in main.items()})
    if len(set(paths.tolist())) >= 2:
        by_path = _grouped_cv(X, y, paths, classes)
        report['leave_one_path_out_accuracy'] = by_path['accuracy']
    if len(classes) >= 3:
        report['unknown_false_accept_rate'] = _open_set_far(X, y, classes)
    return report


def _grouped_cv(X, y, groups, classes) -> dict:
    """Hold out each group in turn; pool predictions into one confusion matrix."""
    idx_of = {c: i for i, c in enumerate(classes)}
    confusion = np.zeros((len(classes), len(classes)), dtype=int)
    raw_scores, true_idx, own_d2 = [], [], []
    skipped = 0
    for g in sorted(set(groups.tolist())):
        test = groups == g
        train = ~test
        train_classes = sorted(set(y[train].tolist()))
        if len(train_classes) < 2:
            skipped += int(np.sum(test))
            continue
        model = ShrinkageLDA().fit(X[train], y[train])
        seen = np.isin(y[test], train_classes)
        skipped += int(np.sum(~seen))
        if not np.any(seen):
            continue
        Xt, yt = X[test][seen], y[test][seen]
        d2 = model.distances(Xt)
        for row, truth in zip(d2, yt):
            pred = model.classes[int(np.argmin(row))]
            confusion[idx_of[truth], idx_of[pred]] += 1
            own_d2.append(float(row[model.classes.index(truth)]))
            if len(model.classes) == len(classes):
                raw_scores.append(-0.5 * row)
                true_idx.append(idx_of[truth])
    return _cv_summary(confusion, classes, raw_scores, true_idx, own_d2, skipped)


def _within_capture_cv(X, y, samples, classes) -> dict:
    idx_of = {c: i for i, c in enumerate(classes)}
    order = np.array([s['index'] for s in samples])
    captures = np.array([s['capture_id'] for s in samples])
    test = np.zeros(len(y), dtype=bool)
    for c in set(captures.tolist()):
        members = np.flatnonzero(captures == c)
        members = members[np.argsort(order[members])]
        n_test = max(1, int(round(0.3 * len(members))))
        test[members[-n_test:]] = True
    confusion = np.zeros((len(classes), len(classes)), dtype=int)
    raw_scores, true_idx, own_d2 = [], [], []
    train = ~test
    if len(set(y[train].tolist())) == len(classes):
        model = ShrinkageLDA().fit(X[train], y[train])
        for row, truth in zip(model.distances(X[test]), y[test]):
            confusion[idx_of[truth], int(np.argmin(row))] += 1
            own_d2.append(float(row[idx_of[truth]]))
            raw_scores.append(-0.5 * row)
            true_idx.append(idx_of[truth])
    return _cv_summary(confusion, classes, raw_scores, true_idx, own_d2, 0)


def _cv_summary(confusion, classes, raw_scores, true_idx, own_d2, skipped) -> dict:
    total = int(confusion.sum())
    correct = int(np.trace(confusion))
    recall = {c: (round(float(confusion[i, i] / confusion[i].sum()), 3)
                  if confusion[i].sum() else None)
              for i, c in enumerate(classes)}
    out = {
        'accuracy': round(correct / total, 3) if total else None,
        'n_tested': total,
        'n_skipped': int(skipped),
        'chance': round(1.0 / len(classes), 3),
        'confusion': {'labels': classes, 'matrix': confusion.tolist()},
        'per_person_recall': recall,
        '_own_d2': own_d2,
    }
    if len(raw_scores) >= 8:
        out['temperature'] = round(_fit_temperature(np.array(raw_scores), np.array(true_idx)), 2)
    return out


def _open_set_far(X, y, classes) -> float | None:
    """Leave-one-person-out: how often is a never-enrolled person accepted as
    their nearest enrolled neighbour? The distance limit here is set from
    training-set distances, which are tighter than the held-out ones the live
    limit uses, so treat this as a lower bound on the live false-accept rate."""
    accepted, total = 0, 0
    for held in classes:
        train = y != held
        model = ShrinkageLDA().fit(X[train], y[train])
        own = model.distances(X[train])[np.arange(int(np.sum(train))),
                                        [model.classes.index(c) for c in y[train]]]
        limit = np.percentile(own, 97.5) * 1.25
        d2 = model.distances(X[~train]).min(axis=1)
        accepted += int(np.sum(d2 <= limit))
        total += int(np.sum(~train))
    return round(accepted / total, 3) if total else None
