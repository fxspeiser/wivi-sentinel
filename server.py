"""
Wi-Vi Sentinel v2 - Local Security API Server
================================================
REST API for WiFi-based biometric detection, classification, and tracking.
"""

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
import numpy as np
import time
import json
import threading
import queue
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

import sys
sys.path.insert(0, BASE_DIR)
from engine.csi_processor import (
    SimulatedCSISource, SignatureExtractor, SignatureMatcher,
    ProfileStore, SpeciesClassifier, SexEstimator, DopplerDirectionDetector,
    DeviceCorrelator, WINDOW_SIZE, CSI_SAMPLE_RATE, WAVELENGTH
)
from engine.device_scanner import DeviceScanner
from engine.config import config_manager, ConfigError, DATA_DIR
from engine.analysis import AnalysisEngine

app = Flask(__name__, static_folder=None)
CORS(app)

DIST_DIR = os.path.join(BASE_DIR, 'dist')
os.makedirs(DATA_DIR, exist_ok=True)


@app.route('/')
def index():
    # Serve Vite build if available, otherwise fall back to CDN Babel version
    if os.path.exists(os.path.join(DIST_DIR, 'index.html')):
        return send_from_directory(DIST_DIR, 'index.html')
    return open(os.path.join(BASE_DIR, 'index.legacy.html')).read()


@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(DIST_DIR, 'assets'), filename)


# ─── CSI Source Selection ────────────────────────────────────────────────────
#
# Set environment variable to switch sources:
#   CSI_SOURCE=simulated  (default — synthetic demo data)
#   CSI_SOURCE=nexmon     (live from Raspberry Pi 4 over UDP)
#   CSI_SOURCE=esp32      (live from ESP32 via USB serial)
#
# For Nexmon, also set:
#   CSI_UDP_PORT=5500     (UDP port the Pi sends to)
#
# For ESP32, also set:
#   ESP32_SERIAL_PORT=/dev/ttyUSB0  (serial port for ESP32)
#   ESP32_BAUD_RATE=115200          (baud rate, default 115200)
#

CSI_MODE = os.environ.get('CSI_SOURCE', 'simulated').lower()

if CSI_MODE == 'nexmon':
    from engine.nexmon_source import NexmonCSISource
    udp_port = int(os.environ.get('CSI_UDP_PORT', '5500'))
    csi_source = NexmonCSISource(udp_port=udp_port)
    print(f"[LIVE] Nexmon CSI source on UDP port {udp_port}")
    print(f"[LIVE] Waiting for Pi 4 to connect...")
elif CSI_MODE == 'esp32':
    from engine.esp32_source import ESP32CSISource
    serial_port = os.environ.get('ESP32_SERIAL_PORT', '/dev/ttyUSB0')
    baud_rate = int(os.environ.get('ESP32_BAUD_RATE', '115200'))
    csi_source = ESP32CSISource(serial_port=serial_port, baud_rate=baud_rate)
    print(f"[LIVE] ESP32 CSI source on {serial_port} @ {baud_rate}")
else:
    csi_source = SimulatedCSISource(n_people=5, seed=42)
    print(f"[SIM] Simulated CSI source (5 entities)")

# ─── Global State ────────────────────────────────────────────────────────────

extractor = SignatureExtractor()
matcher = SignatureMatcher()
store = ProfileStore(os.path.join(DATA_DIR, 'profiles.json'))
species_classifier = SpeciesClassifier()
sex_estimator = SexEstimator()
direction_detector = DopplerDirectionDetector()

# Device name discovery and subject correlation.
# probe_iface: monitor-mode interface on Pi (mon0); None disables probe sniffing.
# mDNS scanning runs regardless when zeroconf is installed.
_probe_iface = os.environ.get('PROBE_IFACE', None)
device_scanner = DeviceScanner(probe_iface=_probe_iface, stale_seconds=120)
device_scanner.start()
device_correlator = DeviceCorrelator(store)

# Pipeline v2: long-window analysis, Signal Lab visuals and roster
# identification. Runs beside the extractors above and never alters their input.
analysis_engine = AnalysisEngine(DATA_DIR, nominal_fs=CSI_SAMPLE_RATE, wavelength_m=WAVELENGTH)

current_detections = []
detection_lock = threading.Lock()
event_queue = queue.Queue(maxsize=100)
detection_history = []

# Persisted, validated settings live in engine/config.py. The detection loop
# takes an immutable snapshot per window so a toggle flipped mid-analysis can
# never yield a half-configured result.
running = True

# Detector switches exposed in the admin UI, with the copy shown beside each one.
DETECTOR_CONTROLS = [
    {'key': 'heartbeat_enabled', 'label': 'Heartbeat sensing',
     'detail': 'Cardiac + respiratory band analysis (0.8-2.5 Hz). Needs a still subject.'},
    {'key': 'gait_enabled', 'label': 'Gait analysis',
     'detail': 'Step cadence and stride periodicity (0.8-4.0 Hz). Needs a moving subject.'},
    {'unreliable': True, 'key': 'direction_detection_enabled', 'label': 'Direction / velocity',
     'detail': 'Doppler approach/recede. Unreliable on single-antenna ESP32 hardware.'},
    {'unreliable': True, 'key': 'species_classification_enabled', 'label': 'Species classification',
     'detail': 'Human / dog / cat inference from rate and harmonic features.'},
    {'sensitive': True, 'key': 'sex_estimation_enabled', 'label': 'Sex estimation',
     'detail': 'Infers a sensitive attribute. Below the measurement floor of this hardware.'},
    {'sensitive': True, 'key': 'device_correlation_enabled', 'label': 'Device name correlation',
     'detail': 'Links nearby device names to body signatures and can auto-name people.'},
    {'key': 'profile_creation_enabled', 'label': 'Automatic profile enrolment',
     'detail': 'When off, known profiles still match but no new profiles are stored.'},
    {'key': 'roster_identification_enabled', 'label': 'Roster identification',
     'detail': 'Names walkers against people enrolled in the Signal Lab. Inert until a model is trained.'},
    {'key': 'use_v2_measurements', 'label': 'Feed v2 rates to classifiers',
     'detail': 'Species and sex use the long-window v2 rates when v2 marks them valid. '
               'Off keeps the original inputs; both are always shown in the Signal Lab.'},
]


def _identity_cfg(snap):
    """Roster-identification settings in the shape AnalysisEngine expects."""
    return {'enabled': snap.roster_identification_enabled,
            'min_confidence': snap.identity_min_confidence,
            'min_margin': snap.identity_min_margin}


def _detector_states(snap):
    """Serialise the detector switches plus their UI copy for the dashboard."""
    return [{**c, 'enabled': getattr(snap, c['key'])} for c in DETECTOR_CONTROLS]


def detection_loop():
    global current_detections, detection_history

    while running:
        try:
            # One immutable settings snapshot per window. A toggle flipped while
            # this window is being analysed applies to the *next* window, never
            # to a half-finished one.
            snap = config_manager.snapshot()

            # Nothing to measure — idle without burning CPU on the extractors.
            if not (snap.heartbeat_enabled or snap.gait_enabled):
                with detection_lock:
                    current_detections = []
                time.sleep(snap.scan_interval)
                continue

            # Only toggle simulated people in sim mode
            if CSI_MODE == 'simulated' and np.random.random() < 0.02:
                idx = np.random.randint(0, len(csi_source.people))
                csi_source.toggle_person(idx)

            frames = csi_source.generate_frames(WINDOW_SIZE)

            # In live mode, we may get fewer frames than requested
            if len(frames) < 20:
                time.sleep(snap.scan_interval)
                continue

            # Pipeline v2 sees the same frames (de-duplicated on its side).
            analysis_engine.ingest(frames)
            v2 = analysis_engine.measurements(_identity_cfg(snap))

            amp_matrix = np.array([f.amplitudes for f in frames])
            phase_matrix = np.array([f.phases for f in frames])

            # Each extractor runs only when its detector is switched on. A
            # disabled detector yields None — never a zero that downstream code
            # could mistake for a measurement.
            hb_result = extractor.extract_heartbeat_signature(amp_matrix) if snap.heartbeat_enabled else None
            gait_result = extractor.extract_gait_signature(amp_matrix) if snap.gait_enabled else None

            _raw_var = float(np.mean(np.var(amp_matrix, axis=0)))
            _bpm = f"{hb_result['bpm']:.0f}" if hb_result else 'off'
            _cad = f"{gait_result['cadence_spm']:.0f}" if gait_result else 'off'
            print(f"[DBG] var={_raw_var:.6f}  bpm={_bpm}  cadence={_cad}")

            # Features shared by the classifiers. Absent when their source
            # detector is off, which the classifiers already treat as "no
            # evidence" rather than as a zero measurement.
            bpm        = hb_result['bpm'] if hb_result else 0.0
            resp_rate  = hb_result['respiratory_rate'] if hb_result else 0.0
            cadence    = gait_result['cadence_spm'] if gait_result else 0.0
            stride_reg = gait_result['stride_regularity'] if gait_result else 0.0
            harmonic   = gait_result.get('harmonic_ratio', 0.0) if gait_result else 0.0
            attenuation = (gait_result or hb_result).get('body_attenuation', 0.0)

            # Optional: let the classifiers use v2's long-window rates, but only
            # the ones v2 itself marks valid. The originals above remain the
            # reported bpm / cadence values either way.
            if snap.use_v2_measurements and v2:
                if hb_result and v2['bpm_valid']:
                    bpm = v2['bpm']
                if hb_result and v2['respiratory_rate_valid']:
                    resp_rate = v2['respiratory_rate']
                if gait_result and v2['cadence_spm_valid']:
                    cadence = v2['cadence_spm']
                    harmonic = v2['harmonic_ratio']

            # ── Direction detection ──
            direction_result = (
                direction_detector.detect_direction(phase_matrix, amp_matrix)
                if snap.direction_detection_enabled else None
            )

            # ── Species classification ──
            species_result = (
                species_classifier.classify(
                    bpm=bpm,
                    resp_rate=resp_rate,
                    cadence=cadence,
                    harmonic_ratio=harmonic,
                    body_attenuation=attenuation,
                )
                if snap.species_classification_enabled else None
            )

            # ── Sex estimation (only meaningful for humans) ──
            # Requires species classification to have identified a human; with
            # species classification off there is no human gate, so it stays off.
            sex_result = None
            if snap.sex_estimation_enabled and species_result and species_result['species'] == 'human':
                sex_result = sex_estimator.estimate(
                    cadence=cadence,
                    stride_regularity=stride_reg,
                    body_attenuation=attenuation,
                    bpm=bpm,
                    respiratory_rate=resp_rate,
                    harmonic_ratio=harmonic,
                )

            now = datetime.now(timezone.utc).isoformat()
            new_detections = []

            # Shared classification metadata. Keys for disabled detectors are
            # omitted entirely so the UI can render "off" rather than a value.
            active_inputs = [n for n, on in (('heartbeat', hb_result is not None),
                                             ('gait', gait_result is not None)) if on]
            classification = {'analysis_inputs': active_inputs}
            if v2:
                # Additive: v2 measurements and any roster identity ride along
                # beside the original fields; nothing existing is overwritten.
                classification['analysis_v2'] = {k: v for k, v in v2.items() if k != 'identity'}
                classification['classifier_inputs'] = 'v2' if snap.use_v2_measurements else 'original'
                if v2.get('identity'):
                    classification['identity'] = v2['identity']
            if species_result:
                classification.update({
                    'species': species_result['species'],
                    'species_confidence': species_result['confidence'],
                    'species_scores': species_result['scores'],
                })
            if sex_result:
                classification.update({
                    'sex_estimation': sex_result['estimation'],
                    'sex_male_prob': sex_result['male_prob'],
                    'sex_female_prob': sex_result['female_prob'],
                    'sex_confidence': sex_result['confidence'],
                })
            if direction_result:
                classification.update({
                    'direction': direction_result['direction'],
                    'direction_confidence': direction_result['confidence'],
                    'radial_velocity': direction_result['radial_velocity'],
                    'speed_mps': direction_result['speed_mps'],
                    'speed_kmh': direction_result['speed_kmh'],
                })

            def _match_and_record(sig_type, signature, metadata):
                """Match a signature against the store and build its detection record.

                Honours profile_creation_enabled: when off, an unmatched
                signature is reported live but never persisted.
                """
                stored = [s for s in store.get_signatures_for_matching()
                          if s['sig_type'] == sig_type]

                best_match, best_score = None, 0.0
                for s in stored:
                    score = matcher.match(signature, s['signature'])
                    if score > best_score:
                        best_score = score
                        best_match = s

                if best_match and best_score >= snap.match_threshold:
                    store.add_or_update(sig_type, signature, metadata,
                                        match_id=best_match['id'], confidence=best_score)
                    return {
                        'type': sig_type, 'status': 'known', 'profile_id': best_match['id'],
                        'nickname': best_match.get('nickname'), 'confidence': best_score,
                        'metadata': metadata, 'timestamp': now,
                    }

                if not snap.profile_creation_enabled:
                    # Live signal, deliberately not enrolled.
                    return {
                        'type': sig_type, 'status': 'unmatched', 'profile_id': None,
                        'nickname': None, 'confidence': 0.0,
                        'metadata': metadata, 'timestamp': now,
                    }

                profile = store.add_or_update(sig_type, signature, metadata)
                return {
                    'type': sig_type, 'status': 'new', 'profile_id': profile['id'],
                    'nickname': None, 'confidence': 0.0,
                    'metadata': metadata, 'timestamp': now,
                }

            # Process heartbeat detection
            if hb_result and hb_result['signal_quality'] > snap.min_signal_quality:
                hb_attn = hb_result.get('body_attenuation', 0)
                hb_distance = round(max(0.5, (1.0 - hb_attn) * 8.0 + 0.5), 1) if hb_attn > 0 else None
                new_detections.append(_match_and_record('heartbeat', hb_result['signature'], {
                    'bpm': hb_result['bpm'],
                    'signal_quality': hb_result['signal_quality'],
                    'respiratory_rate': hb_result['respiratory_rate'],
                    'body_attenuation': hb_attn,
                    'distance_m': hb_distance,
                    **classification,
                }))

            # Process gait detection
            if gait_result and gait_result['signal_quality'] > snap.min_signal_quality:
                attn = gait_result.get('body_attenuation', 0)
                # Distance estimate: body_attenuation ~1 ⇒ very close, ~0 ⇒ far
                distance_m = round(max(0.5, (1.0 - attn) * 8.0 + 0.5), 1) if attn > 0 else None
                new_detections.append(_match_and_record('gait', gait_result['signature'], {
                    'cadence_spm': gait_result['cadence_spm'],
                    'stride_regularity': gait_result['stride_regularity'],
                    'signal_quality': gait_result['signal_quality'],
                    'harmonic_ratio': gait_result.get('harmonic_ratio', 0),
                    'body_attenuation': attn,
                    'distance_m': distance_m,
                    **classification,
                }))

            # ── Device correlation ──────────────────────────────────────────
            if snap.device_correlation_enabled:
                visible_devices = device_scanner.get_visible()
                active_profile_ids = [d['profile_id'] for d in new_detections
                                      if d.get('profile_id') and d.get('metadata', {}).get('species') == 'human']

                device_correlator.record_window(active_profile_ids, visible_devices)

                # Update device candidate lists stored on each profile (for UI display)
                for pid in active_profile_ids:
                    candidates = device_correlator.get_candidates(pid, visible_devices)
                    store.set_device_candidates(pid, candidates)

                # Auto-tag profiles whose suggested device association hit threshold (humans only)
                for pid, display_name, score in device_correlator.check_auto_associations(visible_devices):
                    existing = store.profiles.get(pid, {})
                    if existing.get('metadata', {}).get('species') != 'human':
                        continue
                    if not existing.get('nickname'):
                        store.tag_profile(pid, display_name)
                        print(f"[DeviceCorrelator] Auto-tagged profile {pid} as '{display_name}' (score={score:.3f})")
                        for d in new_detections:
                            if d.get('profile_id') == pid:
                                d['nickname'] = display_name

            with detection_lock:
                current_detections = new_detections
                for d in new_detections:
                    detection_history.append(d)
                    if len(detection_history) > snap.max_history:
                        detection_history = detection_history[-snap.max_history:]

            for d in new_detections:
                try:
                    event_queue.put_nowait(d)
                except queue.Full:
                    try:
                        event_queue.get_nowait()
                        event_queue.put_nowait(d)
                    except:
                        pass

            time.sleep(snap.scan_interval)

        except Exception as e:
            print(f"Detection loop error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)


# ─── API Routes ──────────────────────────────────────────────────────────────

@app.route('/api/status')
def get_status():
    _snap = config_manager.snapshot()
    if hasattr(csi_source, 'active_signal_count'):
        active_count = csi_source.active_signal_count
    elif hasattr(csi_source, 'people'):
        active_count = sum(1 for p in csi_source.people if p['active'])
    else:
        active_count = 0

    result = {
        'status': 'running' if running else 'stopped',
        'csi_source': CSI_MODE,
        'active_signals': active_count,
        'total_profiles': len(store.profiles),
        'config': _snap.to_dict(),
        'detectors': _detector_states(_snap),
        'uptime': time.time(),
    }

    # Add source-specific status
    if hasattr(csi_source, 'get_status_info'):
        result['source_info'] = csi_source.get_status_info()

    return jsonify(result)

@app.route('/api/detections')
def get_detections():
    with detection_lock:
        return jsonify({'current': current_detections, 'recent_history': detection_history[-20:]})

@app.route('/api/profiles')
def get_profiles():
    return jsonify({'profiles': store.get_all()})

@app.route('/api/profiles/tag', methods=['POST'])
def tag_profile():
    data = request.json
    pid, nickname = data.get('profile_id'), data.get('nickname', '').strip()
    if not pid or not nickname:
        return jsonify({'error': 'profile_id and nickname required'}), 400
    result = store.tag_profile(pid, nickname)
    if result:
        return jsonify({'success': True, 'profile': {k: v for k, v in result.items() if k != 'signature'}})
    return jsonify({'error': 'Profile not found'}), 404

@app.route('/api/profiles/<profile_id>', methods=['DELETE'])
def delete_profile(profile_id):
    # If the client sent a combined profile id, also delete the component profiles.
    # The combined entry itself isn't stored — find it by scanning get_all().
    component_ids = []
    for p in store.get_all():
        if p['id'] == profile_id and p.get('sig_type') == 'combined':
            component_ids = p.get('component_ids', [])
            break

    deleted = store.delete_profile(profile_id)
    for cid in component_ids:
        if cid != profile_id:
            store.delete_profile(cid)

    if deleted or component_ids:
        return jsonify({'success': True, 'scrubbed': True})
    return jsonify({'error': 'Profile not found'}), 404

@app.route('/api/stream')
def stream():
    def generate():
        while True:
            try:
                detection = event_queue.get(timeout=5)
                yield f"data: {json.dumps(detection)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat_ping'})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/config', methods=['POST'])
def update_config():
    """Legacy threshold endpoint. Kept for older clients; delegates to the
    validated config manager so both paths share one source of truth."""
    data = request.json or {}
    updates = {k: data[k] for k in ('match_threshold', 'high_confidence', 'scan_interval')
               if k in data}
    try:
        snap = config_manager.update(updates)
    except ConfigError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'config': snap.to_dict()})


# ─── Administration ──────────────────────────────────────────────────────────

@app.route('/api/admin/config', methods=['GET'])
def admin_config_get():
    """Current settings plus the detector switch metadata the admin UI renders."""
    snap = config_manager.snapshot()
    return jsonify({
        'config': snap.to_dict(),
        'detectors': _detector_states(snap),
        'bounds': snap.NUMERIC_BOUNDS,
    })


@app.route('/api/admin/config', methods=['POST'])
def admin_config_set():
    """Apply a partial settings update.

    Accepts any subset of the config fields; unknown keys, wrong types and
    out-of-range numbers are rejected as 400 with an explanatory message.
    Changes are persisted atomically and take effect on the next analysis
    window — heartbeat and gait are independent and neither implies the other.
    """
    global current_detections
    try:
        snap = config_manager.update(request.json or {})
    except ConfigError as e:
        return jsonify({'error': str(e)}), 400

    # Drop live readings belonging to a detector that was just switched off, so
    # the dashboard never shows a stale value under a disabled detector.
    with detection_lock:
        current_detections = [
            d for d in current_detections
            if not (d['type'] == 'heartbeat' and not snap.heartbeat_enabled)
            and not (d['type'] == 'gait' and not snap.gait_enabled)
        ]

    return jsonify({
        'success': True,
        'config': snap.to_dict(),
        'detectors': _detector_states(snap),
    })

@app.route('/api/history')
def get_history():
    limit = request.args.get('limit', 50, type=int)
    sig_type = request.args.get('type', None)
    with detection_lock:
        hist = detection_history[-limit:]
        if sig_type:
            hist = [h for h in hist if h['type'] == sig_type]
    return jsonify({'history': hist})


@app.route('/api/devices')
def get_devices():
    """Currently visible nearby devices and their per-profile correlation scores."""
    visible = device_scanner.get_visible()
    if not config_manager.snapshot().device_correlation_enabled:
        # Scanning still runs, but nothing is linked to a body signature.
        return jsonify({'devices': {k: dict(v, profile_scores={}) for k, v in visible.items()},
                        'count': len(visible), 'correlation_enabled': False})
    # Attach per-profile correlation scores to each device entry
    enriched = {}
    for key, dev in visible.items():
        entry = dict(dev)
        entry['profile_scores'] = {}
        for pid in store.profiles:
            candidates = device_correlator.get_candidates(pid, visible)
            for c in candidates:
                if c['device_key'] == key:
                    entry['profile_scores'][pid] = {
                        'score': c['score'],
                        'sightings': c['sightings'],
                        'suggested': c['suggested'],
                        'nickname': store.profiles[pid].get('nickname'),
                    }
                    break
        enriched[key] = entry
    return jsonify({'devices': enriched, 'count': len(enriched)})


@app.route('/api/devices/suggest', methods=['POST'])
def suggest_device():
    """
    Operator suggests that a profile belongs to a specific nearby device.

    POST body:
        profile_id:    the CSI profile to associate
        device_name:   display name of the device (e.g. "Knibb High Football Rules")

    The system will auto-confirm and tag the profile once co-presence confidence
    exceeds the AUTO_CONFIRM_THRESHOLD (default 0.82) with at least
    AUTO_TAG_MIN_SIGHTINGS (default 10) observations.
    """
    data = request.json or {}
    profile_id = data.get('profile_id', '').strip()
    device_name = data.get('device_name', '').strip()

    if not profile_id or not device_name:
        return jsonify({'error': 'profile_id and device_name required'}), 400

    if profile_id not in store.profiles:
        return jsonify({'error': 'Profile not found'}), 404

    profile_species = store.profiles[profile_id].get('metadata', {}).get('species', '')
    if profile_species != 'human':
        return jsonify({'error': 'Device association is only available for human profiles'}), 400

    if not config_manager.snapshot().device_correlation_enabled:
        return jsonify({'error': 'Device name correlation is disabled in admin settings'}), 409

    device_correlator.suggest(profile_id, device_name)

    # Return current score immediately so the UI can show progress
    visible = device_scanner.get_visible()
    candidates = device_correlator.get_candidates(profile_id, visible)
    current = next((c for c in candidates if c['display_name'] == device_name), None)

    return jsonify({
        'success': True,
        'profile_id': profile_id,
        'device_name': device_name,
        'current_score': current['score'] if current else 0.0,
        'sightings': current['sightings'] if current else 0,
        'auto_confirm_threshold': DeviceCorrelator.AUTO_CONFIRM_THRESHOLD,
        'min_sightings': DeviceCorrelator.AUTO_TAG_MIN_SIGHTINGS,
    })


# ─── Signal Lab: analysis, enrolment, roster model ───────────────────────────

@app.route('/api/analysis')
def get_analysis():
    """Live pipeline-v2 snapshot: signal health, motion state, long-window
    vitals, PSD, motion spectrogram, subcarrier heatmap and roster identity."""
    return jsonify(analysis_engine.snapshot(identity_cfg=_identity_cfg(config_manager.snapshot())))


@app.route('/api/analysis/baseline', methods=['POST', 'DELETE'])
def analysis_baseline():
    """POST with the room empty to calibrate the motion gate; DELETE to return
    to the adaptive floor."""
    if request.method == 'DELETE':
        analysis_engine.clear_baseline()
        return jsonify({'success': True, 'calibrated': False})
    try:
        return jsonify({'success': True, 'calibrated': True, **analysis_engine.set_baseline()})
    except ValueError as e:
        return jsonify({'error': str(e)}), 409


@app.route('/api/enroll', methods=['GET'])
def enroll_status():
    """Roster summary plus the state of any capture in progress."""
    return jsonify({'roster': analysis_engine.enrollment.summary(),
                    'capture': analysis_engine.capture_status()})


@app.route('/api/enroll/start', methods=['POST'])
def enroll_start():
    """Begin a labelled walk capture.

    POST body: name, session (e.g. "day1"), path (e.g. "A"), duration_s,
    and optional enrolled attributes: sex, species.
    """
    data = request.json or {}
    try:
        duration = float(data.get('duration_s', 20))
    except (TypeError, ValueError):
        return jsonify({'error': 'duration_s must be a number'}), 400
    attrs = {k: str(data[k]).strip()[:20] for k in ('sex', 'species') if data.get(k)}
    try:
        status = analysis_engine.start_capture(
            str(data.get('name', '')), str(data.get('session', '')),
            str(data.get('path', '')), duration, attrs)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'success': True, 'capture': status})


@app.route('/api/enroll/stop', methods=['POST'])
def enroll_stop():
    """End the active capture early. Body {"discard": true} throws it away."""
    discard = bool((request.json or {}).get('discard', False))
    return jsonify({'success': True, 'capture': analysis_engine.stop_capture(discard=discard)})


@app.route('/api/enroll/<label>', methods=['DELETE'])
def enroll_delete(label):
    """Remove one person's enrolment (samples, attributes, raw recordings)."""
    if analysis_engine.delete_person(label):
        return jsonify({'success': True})
    return jsonify({'error': 'No such enrolled person'}), 404


@app.route('/api/model/train', methods=['POST'])
def model_train():
    """Refit the roster classifier on everything enrolled and cross-validate it."""
    return jsonify(analysis_engine.train())


@app.route('/api/model/report')
def model_report():
    """Latest validation report: protocol, accuracy, confusion matrix, embedding."""
    return jsonify(analysis_engine.identifier.report)


# ─── ESP32 WiFi Configuration ────────────────────────────────────────────────

@app.route('/api/esp32/wifi', methods=['POST'])
def esp32_wifi_set():
    """Set WiFi credentials on the ESP32 via serial command."""
    if CSI_MODE != 'esp32':
        return jsonify({'error': 'ESP32 source not active'}), 400

    data = request.json or {}
    ssid = data.get('ssid', '').strip()
    password = data.get('password', '')

    if not ssid:
        return jsonify({'error': 'SSID is required'}), 400

    result = csi_source.set_wifi_credentials(ssid, password)
    return jsonify(result), 200 if result['success'] else 500


@app.route('/api/esp32/wifi', methods=['GET'])
def esp32_wifi_status():
    """Get current WiFi connection status from the ESP32."""
    if CSI_MODE != 'esp32':
        return jsonify({'error': 'ESP32 source not active'}), 400

    result = csi_source.get_wifi_status()
    return jsonify(result)


# ─── Startup ─────────────────────────────────────────────────────────────────

detector_thread = threading.Thread(target=detection_loop, daemon=True)
detector_thread.start()


def analysis_feed_loop():
    """Keep pipeline v2 topped up between detection windows.

    Live sources hand out a non-destructive view of their ring buffer, so
    polling it at 5 Hz makes the Signal Lab scroll smoothly instead of once
    per scan interval. The simulated source is fed only by the detection loop:
    reading it generates new data.
    """
    while running:
        try:
            analysis_engine.ingest(csi_source.generate_frames(WINDOW_SIZE))
        except Exception as e:
            print(f"Analysis feed error: {e}")
            time.sleep(1)
        time.sleep(0.2)


if CSI_MODE != 'simulated':
    threading.Thread(target=analysis_feed_loop, daemon=True).start()

if __name__ == '__main__':
    flask_port = int(os.environ.get('FLASK_PORT', 5555))
    print(f"Wi-Vi Sentinel v2 API starting on :{flask_port}")
    print(f"Data directory: {DATA_DIR}")
    app.run(host='0.0.0.0', port=flask_port, debug=False, threaded=True)