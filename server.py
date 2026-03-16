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
    DeviceCorrelator,
    MATCH_THRESHOLD, HIGH_CONFIDENCE, WINDOW_SIZE, N_SUBCARRIERS
)
from engine.device_scanner import DeviceScanner

app = Flask(__name__, static_folder=None)
CORS(app)

DATA_DIR = os.path.join(BASE_DIR, 'data')
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

current_detections = []
detection_lock = threading.Lock()
event_queue = queue.Queue(maxsize=100)
detection_history = []

config = {
    'match_threshold': MATCH_THRESHOLD,
    'high_confidence': HIGH_CONFIDENCE,
    'scan_interval': 1.0,
    'adaptive_threshold': True,
    'max_history': 200,
}

running = True


def detection_loop():
    global current_detections, detection_history

    while running:
        try:
            # Only toggle simulated people in sim mode
            if CSI_MODE == 'simulated' and np.random.random() < 0.02:
                idx = np.random.randint(0, len(csi_source.people))
                csi_source.toggle_person(idx)

            frames = csi_source.generate_frames(WINDOW_SIZE)

            # In live mode, we may get fewer frames than requested
            if len(frames) < 20:
                time.sleep(config['scan_interval'])
                continue

            amp_matrix = np.array([f.amplitudes for f in frames])
            phase_matrix = np.array([f.phases for f in frames])

            hb_result = extractor.extract_heartbeat_signature(amp_matrix)
            gait_result = extractor.extract_gait_signature(amp_matrix)

            _attn = gait_result.get('body_attenuation', 0)
            _dist = round(max(0.5, (1.0 - _attn) * 8.0 + 0.5), 1) if _attn > 0 else None
            _raw_var = float(np.mean(np.var(amp_matrix, axis=0)))
            print(f"[DBG] var={_raw_var:.6f}  attn={_attn:.3f}  dist={_dist}  bpm={hb_result['bpm']:.0f}  cadence={gait_result['cadence_spm']:.0f}")

            # ── Direction detection ──
            direction_result = direction_detector.detect_direction(phase_matrix, amp_matrix)

            # ── Species classification ──
            species_result = species_classifier.classify(
                bpm=hb_result['bpm'],
                resp_rate=hb_result['respiratory_rate'],
                cadence=gait_result['cadence_spm'],
                harmonic_ratio=gait_result.get('harmonic_ratio', 0.5),
                body_attenuation=gait_result.get('body_attenuation', 0.5),
            )

            # ── Sex estimation (only meaningful for humans) ──
            sex_result = {'estimation': 'n/a', 'male_prob': 0.5, 'female_prob': 0.5, 'confidence': 0.0}
            if species_result['species'] == 'human':
                sex_result = sex_estimator.estimate(
                    cadence=gait_result['cadence_spm'],
                    stride_regularity=gait_result['stride_regularity'],
                    body_attenuation=gait_result.get('body_attenuation', 0.5),
                    bpm=hb_result['bpm'],
                )

            now = datetime.now(timezone.utc).isoformat()
            new_detections = []

            # Shared classification metadata
            classification = {
                'species': species_result['species'],
                'species_confidence': species_result['confidence'],
                'species_scores': species_result['scores'],
                'sex_estimation': sex_result['estimation'],
                'sex_male_prob': sex_result['male_prob'],
                'sex_female_prob': sex_result['female_prob'],
                'sex_confidence': sex_result['confidence'],
                'direction': direction_result['direction'],
                'direction_confidence': direction_result['confidence'],
                'radial_velocity': direction_result['radial_velocity'],
                'speed_mps': direction_result['speed_mps'],
                'speed_kmh': direction_result['speed_kmh'],
            }

            # Process heartbeat detection
            if hb_result['signal_quality'] > 0.15:
                hb_sig = hb_result['signature']
                stored = store.get_signatures_for_matching()
                hb_stored = [s for s in stored if s['sig_type'] == 'heartbeat']

                best_match, best_score = None, 0.0
                for s in hb_stored:
                    score = matcher.match(hb_sig, s['signature'])
                    if score > best_score:
                        best_score = score
                        best_match = s

                threshold = config['match_threshold']
                hb_attn = hb_result.get('body_attenuation', 0)
                hb_distance = round(max(0.5, (1.0 - hb_attn) * 8.0 + 0.5), 1) if hb_attn > 0 else None
                metadata = {
                    'bpm': hb_result['bpm'],
                    'signal_quality': hb_result['signal_quality'],
                    'respiratory_rate': hb_result['respiratory_rate'],
                    'body_attenuation': hb_attn,
                    'distance_m': hb_distance,
                    **classification,
                }

                if best_match and best_score >= threshold:
                    profile = store.add_or_update('heartbeat', hb_sig, metadata, match_id=best_match['id'], confidence=best_score)
                    detection = {
                        'type': 'heartbeat', 'status': 'known', 'profile_id': best_match['id'],
                        'nickname': best_match.get('nickname'), 'confidence': best_score,
                        'metadata': metadata, 'timestamp': now,
                    }
                else:
                    profile = store.add_or_update('heartbeat', hb_sig, metadata)
                    detection = {
                        'type': 'heartbeat', 'status': 'new', 'profile_id': profile['id'],
                        'nickname': None, 'confidence': 0.0, 'metadata': metadata, 'timestamp': now,
                    }
                new_detections.append(detection)

            # Process gait detection
            if gait_result['signal_quality'] > 0.15:
                gait_sig = gait_result['signature']
                stored = store.get_signatures_for_matching()
                gait_stored = [s for s in stored if s['sig_type'] == 'gait']

                best_match, best_score = None, 0.0
                for s in gait_stored:
                    score = matcher.match(gait_sig, s['signature'])
                    if score > best_score:
                        best_score = score
                        best_match = s

                threshold = config['match_threshold']
                attn = gait_result.get('body_attenuation', 0)
                # Distance estimate: body_attenuation ~1 ⇒ very close, ~0 ⇒ far
                distance_m = round(max(0.5, (1.0 - attn) * 8.0 + 0.5), 1) if attn > 0 else None
                metadata = {
                    'cadence_spm': gait_result['cadence_spm'],
                    'stride_regularity': gait_result['stride_regularity'],
                    'signal_quality': gait_result['signal_quality'],
                    'harmonic_ratio': gait_result.get('harmonic_ratio', 0),
                    'body_attenuation': attn,
                    'distance_m': distance_m,
                    **classification,
                }

                if best_match and best_score >= threshold:
                    profile = store.add_or_update('gait', gait_sig, metadata, match_id=best_match['id'], confidence=best_score)
                    detection = {
                        'type': 'gait', 'status': 'known', 'profile_id': best_match['id'],
                        'nickname': best_match.get('nickname'), 'confidence': best_score,
                        'metadata': metadata, 'timestamp': now,
                    }
                else:
                    profile = store.add_or_update('gait', gait_sig, metadata)
                    detection = {
                        'type': 'gait', 'status': 'new', 'profile_id': profile['id'],
                        'nickname': None, 'confidence': 0.0, 'metadata': metadata, 'timestamp': now,
                    }
                new_detections.append(detection)

            # ── Device correlation ──────────────────────────────────────────
            visible_devices = device_scanner.get_visible()
            active_profile_ids = [d['profile_id'] for d in new_detections if d.get('profile_id')]

            device_correlator.record_window(active_profile_ids, visible_devices)

            # Update device candidate lists stored on each profile (for UI display)
            for pid in active_profile_ids:
                candidates = device_correlator.get_candidates(pid, visible_devices)
                store.set_device_candidates(pid, candidates)

            # Auto-tag profiles whose suggested device association hit threshold
            for pid, display_name, score in device_correlator.check_auto_associations(visible_devices):
                existing = store.profiles.get(pid, {})
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
                    if len(detection_history) > config['max_history']:
                        detection_history = detection_history[-config['max_history']:]

            for d in new_detections:
                try:
                    event_queue.put_nowait(d)
                except queue.Full:
                    try:
                        event_queue.get_nowait()
                        event_queue.put_nowait(d)
                    except:
                        pass

            time.sleep(config['scan_interval'])

        except Exception as e:
            print(f"Detection loop error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)


# ─── API Routes ──────────────────────────────────────────────────────────────

@app.route('/api/status')
def get_status():
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
        'config': config,
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
    if store.delete_profile(profile_id):
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
    data = request.json
    for key in ('match_threshold', 'high_confidence', 'scan_interval'):
        if key in data:
            config[key] = float(data[key])
    return jsonify({'config': config})

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

if __name__ == '__main__':
    flask_port = int(os.environ.get('FLASK_PORT', 5555))
    print(f"Wi-Vi Sentinel v2 API starting on :{flask_port}")
    print(f"Data directory: {DATA_DIR}")
    app.run(host='0.0.0.0', port=flask_port, debug=False, threaded=True)