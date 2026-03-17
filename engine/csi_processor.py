"""
CSI Signal Processing Engine v2
=================================
Processes WiFi Channel State Information (CSI) to extract:
  - Heartbeat signatures (0.8-2.5 Hz cardiac band)
  - Gait signatures (Doppler shift + movement pattern classification)
  - Species classification (human vs animal via cardiac/respiratory/gait analysis)
  - Sex estimation (male/female via gait cadence, stride, body attenuation)
  - Direction of travel (approaching/receding via Doppler phase shift)

Research references:
  - Wi-Vi: Adib & Katabi, SIGCOMM 2013 (through-wall tracking)
  - WiGait: Hsu et al., MobiCom 2017 (gait velocity from WiFi)
  - BodyScan: Zheng et al., ACM MobiSys 2020 (body composition via WiFi CSI)
  - WiStep: Wang et al., IEEE TMC 2021 (gait-based sex classification)
  - PhaseBeat: Wang et al., ACM MobiCom 2017 (cardiac monitoring via phase)

Architecture supports pluggable CSI sources:
  - SimulatedCSISource (demo mode with realistic synthetic data)
  - LinuxCSIToolSource (Linux 802.11n CSI Tool - Atheros)
  - NexmonCSISource (Nexmon firmware - Broadcom)
"""

import numpy as np
from scipy import signal as sig
from scipy.spatial.distance import euclidean
from scipy.interpolate import interp1d
import time
import hashlib
import json
import os
import threading
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

CSI_SAMPLE_RATE = 100        # Hz - CSI packet rate
HEARTBEAT_BAND = (0.8, 2.5)  # Hz - covers 48-150 BPM
RESPIRATION_BAND = (0.1, 0.5) # Hz - respiratory
GAIT_CYCLE_BAND = (0.8, 4.0)  # Hz - walking cadence (0.8 Hz = 48 spm floor, cuts env noise)
WINDOW_SIZE = 512             # samples per analysis window
HOP_SIZE = 64                 # overlap hop
N_SUBCARRIERS = 56            # 802.11n 20MHz
SIGNATURE_LENGTH = 128        # compressed signature vector length
MATCH_THRESHOLD = 0.60        # cosine similarity threshold for positive ID
HIGH_CONFIDENCE = 0.85
DTW_MAX_WARP = 15             # DTW warping constraint

# Carrier frequency for Doppler calculation
# ESP32 uses 2.4 GHz (channel 11 = 2462 MHz); Pi Nexmon typically uses 5 GHz
SPEED_OF_LIGHT = 3e8
_FREQ_2_4GHZ = 2.462e9   # channel 11
_FREQ_5GHZ = 5.18e9      # channel 36

def _get_carrier_freq():
    """Pick carrier frequency based on CSI_SOURCE env var."""
    import os
    src = os.environ.get('CSI_SOURCE', 'simulated').lower()
    return _FREQ_2_4GHZ if src == 'esp32' else _FREQ_5GHZ

CARRIER_FREQ_HZ = _get_carrier_freq()
WAVELENGTH = SPEED_OF_LIGHT / CARRIER_FREQ_HZ

# ─── Species Classification Thresholds ───────────────────────────────────────
# Based on comparative physiology:
#   Human resting HR:   60-100 BPM  | Resp: 12-20 bpm  | Bipedal gait 85-140 spm
#   Dog HR:             60-140 BPM  | Resp: 15-30 bpm  | Quadruped gait 150-250 spm
#   Cat HR:            120-240 BPM  | Resp: 20-30 bpm  | Quadruped gait 170-280 spm
#   Large animals:      28-80 BPM   | Resp:  8-15 bpm  | Gait varies

SPECIES_PROFILES = {
    'human': {
        'hr_range': (48, 110),
        'resp_range': (10, 24),
        'cadence_range': (70, 150),
        'bipedal': True,
        'gait_harmonic_ratio': (0.3, 0.7),  # 2nd/1st harmonic - bipedal symmetry
        'prior': 1.0,                        # baseline prior (most common detection)
    },
    'dog': {
        'hr_range': (55, 160),
        'resp_range': (14, 35),
        'cadence_range': (140, 280),
        'bipedal': False,
        'gait_harmonic_ratio': (0.1, 0.35),  # quadruped has lower 2nd harmonic
        'prior': 0.6,                        # needs stronger evidence to beat human
    },
    'cat': {
        'hr_range': (110, 260),
        'resp_range': (18, 35),
        'cadence_range': (160, 300),
        'bipedal': False,
        'gait_harmonic_ratio': (0.05, 0.3),
        'prior': 0.5,
    },
    'large_animal': {
        'hr_range': (25, 85),
        'resp_range': (6, 18),
        'cadence_range': (40, 120),
        'bipedal': False,
        'gait_harmonic_ratio': (0.15, 0.5),
        'prior': 0.4,                        # significant overlap with noisy human cadence
    },
}

# ─── Sex Estimation Thresholds ───────────────────────────────────────────────
# Based on WiStep (Wang et al. IEEE TMC 2021) and biomechanics literature:
#   Males:   cadence 100-120 spm, stride 0.70-0.85m, higher body attenuation
#   Females: cadence 110-130 spm, stride 0.55-0.72m, lower body attenuation
# Plus cardiac differences: female resting HR tends 2-8 BPM higher

SEX_FEATURES = {
    'male': {
        'cadence_center': 108,
        'stride_regularity_center': 0.65,
        'body_attenuation_center': 0.65,  # higher mass = more signal impact
        'hr_offset': 0,
    },
    'female': {
        'cadence_center': 118,
        'stride_regularity_center': 0.60,
        'body_attenuation_center': 0.45,
        'hr_offset': 4,  # avg BPM higher
    },
}


class CSIFrame:
    """Single CSI measurement frame across all subcarriers."""
    def __init__(self, timestamp: float, amplitudes: np.ndarray, phases: np.ndarray,
                 rssi: float = 0.0, raw_amplitude: float = 0.0):
        self.timestamp = timestamp
        self.amplitudes = amplitudes  # shape: (N_SUBCARRIERS,)
        self.phases = phases          # shape: (N_SUBCARRIERS,)
        self.rssi = rssi              # dBm from CSI_DATA header
        self.raw_amplitude = raw_amplitude  # pre-normalization max amplitude


# ═════════════════════════════════════════════════════════════════════════════
# CLASSIFIERS
# ═════════════════════════════════════════════════════════════════════════════

class SpeciesClassifier:
    """
    Classifies detected entity as human or animal species using multi-band
    biometric analysis. Uses cardiac rate, respiratory rate, gait cadence,
    and gait harmonic structure.
    
    Approach:
      1. Score each species profile against observed biometrics
      2. Weight by feature reliability (cardiac > gait > respiratory)
      3. Return top classification with confidence
    """

    @staticmethod
    def classify(bpm: float, resp_rate: float, cadence: float,
                 harmonic_ratio: float = 0.5, body_attenuation: float = 0.5) -> dict:
        """
        Returns: {species: str, confidence: float, scores: dict}
        """
        scores = {}

        for species, prof in SPECIES_PROFILES.items():
            score = 0.0
            weights_sum = 0.0

            # Cardiac rate match (weight: 3.0)
            if bpm > 0:
                hr_mid = (prof['hr_range'][0] + prof['hr_range'][1]) / 2.0
                hr_span = (prof['hr_range'][1] - prof['hr_range'][0]) / 2.0
                hr_dist = abs(bpm - hr_mid) / (hr_span + 1e-10)
                hr_score = max(0, 1.0 - hr_dist * 0.5)
                # Bonus if within range
                if prof['hr_range'][0] <= bpm <= prof['hr_range'][1]:
                    hr_score = max(hr_score, 0.7)
                score += hr_score * 3.0
                weights_sum += 3.0

            # Respiratory rate match (weight: 1.5)
            if resp_rate > 0:
                resp_mid = (prof['resp_range'][0] + prof['resp_range'][1]) / 2.0
                resp_span = (prof['resp_range'][1] - prof['resp_range'][0]) / 2.0
                resp_dist = abs(resp_rate - resp_mid) / (resp_span + 1e-10)
                resp_score = max(0, 1.0 - resp_dist * 0.5)
                if prof['resp_range'][0] <= resp_rate <= prof['resp_range'][1]:
                    resp_score = max(resp_score, 0.6)
                score += resp_score * 1.5
                weights_sum += 1.5

            # Gait cadence match (weight: 2.5)
            if cadence > 0:
                cad_mid = (prof['cadence_range'][0] + prof['cadence_range'][1]) / 2.0
                cad_span = (prof['cadence_range'][1] - prof['cadence_range'][0]) / 2.0
                cad_dist = abs(cadence - cad_mid) / (cad_span + 1e-10)
                cad_score = max(0, 1.0 - cad_dist * 0.5)
                if prof['cadence_range'][0] <= cadence <= prof['cadence_range'][1]:
                    cad_score = max(cad_score, 0.65)
                score += cad_score * 2.5
                weights_sum += 2.5

            # Harmonic ratio (bipedal vs quadruped) (weight: 2.0)
            if harmonic_ratio > 0:
                hr_lo, hr_hi = prof['gait_harmonic_ratio']
                if hr_lo <= harmonic_ratio <= hr_hi:
                    harm_score = 0.8
                else:
                    harm_dist = min(abs(harmonic_ratio - hr_lo), abs(harmonic_ratio - hr_hi))
                    harm_score = max(0, 0.8 - harm_dist * 2.0)
                score += harm_score * 2.0
                weights_sum += 2.0

            scores[species] = (score / (weights_sum + 1e-10)) * prof.get('prior', 1.0)

        # Determine winner
        best_species = max(scores, key=scores.get)
        best_score = scores[best_species]

        # Confidence based on margin over second best
        sorted_scores = sorted(scores.values(), reverse=True)
        margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        confidence = min(1.0, best_score * (0.5 + margin))

        return {
            'species': best_species,
            'confidence': round(float(confidence), 3),
            'scores': {k: round(v, 3) for k, v in scores.items()},
        }


class SexEstimator:
    """
    Estimates biological sex from WiFi CSI biometric features.
    Based on WiStep methodology: gait cadence, stride characteristics,
    body attenuation profile, and resting heart rate differences.
    
    Returns probabilistic estimate, not binary classification.
    """

    @staticmethod
    def estimate(cadence: float, stride_regularity: float,
                 body_attenuation: float, bpm: float = 0.0) -> dict:
        """
        Returns: {estimation: str, male_prob: float, female_prob: float, confidence: float}
        """
        male_score = 0.0
        female_score = 0.0
        feature_count = 0

        # Cadence analysis (females tend higher cadence at same speed)
        if cadence > 0:
            m_cad_dist = abs(cadence - SEX_FEATURES['male']['cadence_center'])
            f_cad_dist = abs(cadence - SEX_FEATURES['female']['cadence_center'])
            # Gaussian-like scoring
            male_score += np.exp(-0.5 * (m_cad_dist / 12.0) ** 2)
            female_score += np.exp(-0.5 * (f_cad_dist / 12.0) ** 2)
            feature_count += 1

        # Stride regularity (males tend higher due to longer legs / more consistent gait)
        if stride_regularity > 0:
            m_sr_dist = abs(stride_regularity - SEX_FEATURES['male']['stride_regularity_center'])
            f_sr_dist = abs(stride_regularity - SEX_FEATURES['female']['stride_regularity_center'])
            male_score += np.exp(-0.5 * (m_sr_dist / 0.12) ** 2)
            female_score += np.exp(-0.5 * (f_sr_dist / 0.12) ** 2)
            feature_count += 1

        # Body attenuation (proxy for body mass - males generally higher)
        if body_attenuation > 0:
            m_ba_dist = abs(body_attenuation - SEX_FEATURES['male']['body_attenuation_center'])
            f_ba_dist = abs(body_attenuation - SEX_FEATURES['female']['body_attenuation_center'])
            male_score += np.exp(-0.5 * (m_ba_dist / 0.15) ** 2) * 0.8  # lower weight - less reliable
            female_score += np.exp(-0.5 * (f_ba_dist / 0.15) ** 2) * 0.8
            feature_count += 1

        # Heart rate offset (females avg ~4 BPM higher at rest)
        if bpm > 40:
            # Center around population average ~72 BPM
            male_hr_expected = 70
            female_hr_expected = 74
            m_hr_dist = abs(bpm - male_hr_expected)
            f_hr_dist = abs(bpm - female_hr_expected)
            male_score += np.exp(-0.5 * (m_hr_dist / 15.0) ** 2) * 0.6
            female_score += np.exp(-0.5 * (f_hr_dist / 15.0) ** 2) * 0.6
            feature_count += 1

        if feature_count == 0:
            return {
                'estimation': 'unknown',
                'male_prob': 0.5,
                'female_prob': 0.5,
                'confidence': 0.0,
            }

        # Normalize to probabilities
        total = male_score + female_score + 1e-10
        male_prob = male_score / total
        female_prob = female_score / total

        # Confidence based on separation and feature count
        separation = abs(male_prob - female_prob)
        feature_factor = min(1.0, feature_count / 3.0)
        confidence = separation * feature_factor

        estimation = 'male' if male_prob > female_prob else 'female'
        if confidence < 0.15:
            estimation = 'indeterminate'

        return {
            'estimation': estimation,
            'male_prob': round(float(male_prob), 3),
            'female_prob': round(float(female_prob), 3),
            'confidence': round(float(confidence), 3),
        }


class DopplerDirectionDetector:
    """
    Detects direction of travel (approaching/receding) using WiFi CSI phase
    information and Doppler frequency shift analysis.
    
    Based on Fresnel zone model (Wang et al., MobiCom 2016):
      - Approaching target: positive Doppler shift (phase decreasing)
      - Receding target: negative Doppler shift (phase increasing)
      - Stationary: no consistent phase drift
    
    Uses unwrapped phase across subcarriers to estimate radial velocity.
    """

    @staticmethod
    def detect_direction(phase_matrix: np.ndarray, amplitude_matrix: np.ndarray) -> dict:
        """
        Analyze phase evolution to determine movement direction.
        
        Args:
            phase_matrix: shape (n_samples, N_SUBCARRIERS) - raw phase per frame
            amplitude_matrix: shape (n_samples, N_SUBCARRIERS)
            
        Returns:
            dict with direction, radial_velocity, confidence
        """
        n_samples = phase_matrix.shape[0]
        if n_samples < 10:
            return {'direction': 'unknown', 'radial_velocity': 0.0, 'confidence': 0.0}

        # Unwrap phase per subcarrier to remove 2π discontinuities
        unwrapped = np.unwrap(phase_matrix, axis=0)

        # Remove linear phase offset across subcarriers (timing offset)
        # Use amplitude-weighted average phase rate
        amp_weights = np.mean(amplitude_matrix, axis=0)
        amp_weights = amp_weights / (np.sum(amp_weights) + 1e-10)

        # Compute phase rate of change per subcarrier
        phase_rate = np.diff(unwrapped, axis=0) * CSI_SAMPLE_RATE  # rad/s

        # Weighted average phase rate across subcarriers
        weighted_rate = np.sum(phase_rate * amp_weights[np.newaxis, :], axis=1)

        # Smooth with moving average
        kernel_size = min(20, len(weighted_rate) // 3)
        if kernel_size > 1:
            kernel = np.ones(kernel_size) / kernel_size
            smoothed_rate = np.convolve(weighted_rate, kernel, mode='valid')
        else:
            smoothed_rate = weighted_rate

        # Average Doppler frequency shift
        mean_doppler = np.mean(smoothed_rate)  # rad/s
        doppler_hz = mean_doppler / (2 * np.pi)  # Hz

        # Convert to radial velocity: v = λ * f_doppler / 2
        radial_velocity = WAVELENGTH * doppler_hz / 2.0  # m/s

        # Determine direction
        # Positive radial velocity = approaching (path length decreasing)
        # Negative = receding
        velocity_threshold = 0.05  # m/s minimum to declare direction

        if abs(radial_velocity) < velocity_threshold:
            direction = 'stationary'
        elif radial_velocity > 0:
            direction = 'approaching'
        else:
            direction = 'receding'

        # Confidence based on consistency of phase rate sign
        if len(smoothed_rate) > 0:
            sign_consistency = abs(np.mean(np.sign(smoothed_rate)))
            amplitude_change = np.mean(amplitude_matrix[-n_samples//4:]) - np.mean(amplitude_matrix[:n_samples//4])
            # Cross-validate: approaching should also show amplitude increase
            if direction == 'approaching' and amplitude_change > 0:
                amp_agreement = 1.0
            elif direction == 'receding' and amplitude_change < 0:
                amp_agreement = 1.0
            elif direction == 'stationary':
                amp_agreement = 1.0
            else:
                amp_agreement = 0.5  # phase and amplitude disagree

            confidence = sign_consistency * 0.6 + amp_agreement * 0.4
        else:
            confidence = 0.0

        # Speed estimate
        speed_mps = abs(radial_velocity)

        return {
            'direction': direction,
            'radial_velocity': round(float(radial_velocity), 4),
            'speed_mps': round(float(speed_mps), 3),
            'speed_kmh': round(float(speed_mps * 3.6), 2),
            'confidence': round(float(min(1.0, confidence)), 3),
            'doppler_hz': round(float(doppler_hz), 3),
        }


# ═════════════════════════════════════════════════════════════════════════════
# SIGNATURE EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

class SignatureExtractor:
    """Extracts biometric signatures from CSI stream buffers."""

    def __init__(self):
        self._design_filters()

    def _design_filters(self):
        nyq = CSI_SAMPLE_RATE / 2.0
        self.hb_b, self.hb_a = sig.butter(4, [HEARTBEAT_BAND[0]/nyq, HEARTBEAT_BAND[1]/nyq], btype='band')
        self.resp_b, self.resp_a = sig.butter(3, [RESPIRATION_BAND[0]/nyq, RESPIRATION_BAND[1]/nyq], btype='band')
        self.gait_b, self.gait_a = sig.butter(4, [GAIT_CYCLE_BAND[0]/nyq, GAIT_CYCLE_BAND[1]/nyq], btype='band')

    def extract_heartbeat_signature(self, amplitude_matrix: np.ndarray) -> dict:
        centered = amplitude_matrix - np.mean(amplitude_matrix, axis=0)
        try:
            U, S, Vt = np.linalg.svd(centered, full_matrices=False)
            pc1 = U[:, 0] * S[0]
        except np.linalg.LinAlgError:
            pc1 = np.mean(centered, axis=1)

        cardiac = sig.filtfilt(self.hb_b, self.hb_a, pc1)
        respiratory = sig.filtfilt(self.resp_b, self.resp_a, pc1)

        freqs, psd = sig.welch(cardiac, fs=CSI_SAMPLE_RATE, nperseg=min(256, len(cardiac)))
        hb_mask = (freqs >= HEARTBEAT_BAND[0]) & (freqs <= HEARTBEAT_BAND[1])
        if np.any(hb_mask):
            peak_freq = freqs[hb_mask][np.argmax(psd[hb_mask])]
            bpm = peak_freq * 60.0
            snr = np.max(psd[hb_mask]) / (np.mean(psd) + 1e-10)
        else:
            bpm = 0.0
            snr = 0.0

        hb_psd = psd[hb_mask] if np.any(hb_mask) else np.zeros(10)
        hb_psd = hb_psd / (np.max(hb_psd) + 1e-10)

        autocorr = np.correlate(cardiac, cardiac, mode='full')
        autocorr = autocorr[len(autocorr)//2:]
        autocorr = autocorr[:SIGNATURE_LENGTH//2] / (autocorr[0] + 1e-10)

        sig_vector = np.zeros(SIGNATURE_LENGTH)
        if len(hb_psd) > 1:
            f_interp = interp1d(np.linspace(0, 1, len(hb_psd)), hb_psd, kind='linear')
            sig_vector[:SIGNATURE_LENGTH//2] = f_interp(np.linspace(0, 1, SIGNATURE_LENGTH//2))
        ac_len = min(len(autocorr), SIGNATURE_LENGTH//2)
        sig_vector[SIGNATURE_LENGTH//2:SIGNATURE_LENGTH//2+ac_len] = autocorr[:ac_len]

        norm = np.linalg.norm(sig_vector)
        if norm > 0:
            sig_vector = sig_vector / norm

        quality = min(1.0, snr / 20.0)
        resp_rate = self._estimate_resp_rate(respiratory)

        # Body attenuation (same formula as gait extraction)
        raw_var = float(np.mean(np.var(amplitude_matrix, axis=0)))
        body_attenuation = float(np.clip((np.log10(raw_var + 1e-6) + 4.0) / 1.0, 0.0, 1.0))

        return {
            'signature': sig_vector.tolist(),
            'bpm': round(float(bpm), 1),
            'signal_quality': round(float(quality), 3),
            'respiratory_rate': round(float(resp_rate), 1),
            'body_attenuation': round(float(body_attenuation), 4),
        }

    def _estimate_resp_rate(self, respiratory_signal: np.ndarray) -> float:
        freqs, psd = sig.welch(respiratory_signal, fs=CSI_SAMPLE_RATE, nperseg=min(256, len(respiratory_signal)))
        resp_mask = (freqs >= RESPIRATION_BAND[0]) & (freqs <= RESPIRATION_BAND[1])
        if np.any(resp_mask) and np.max(psd[resp_mask]) > 0:
            return freqs[resp_mask][np.argmax(psd[resp_mask])] * 60.0
        return 0.0

    def extract_gait_signature(self, amplitude_matrix: np.ndarray) -> dict:
        centered = amplitude_matrix - np.mean(amplitude_matrix, axis=0)
        velocity = np.diff(centered, axis=0)

        try:
            U, S, Vt = np.linalg.svd(velocity, full_matrices=False)
            n_components = min(3, U.shape[1])
            components = U[:, :n_components] * S[:n_components]
        except np.linalg.LinAlgError:
            components = np.mean(velocity, axis=1, keepdims=True)
            n_components = 1

        gait_signal = sig.filtfilt(self.gait_b, self.gait_a, components[:, 0])

        freqs, psd = sig.welch(gait_signal, fs=CSI_SAMPLE_RATE, nperseg=min(256, len(gait_signal)))
        gait_mask = (freqs >= GAIT_CYCLE_BAND[0]) & (freqs <= GAIT_CYCLE_BAND[1])

        if np.any(gait_mask) and np.max(psd[gait_mask]) > 0:
            peak_freq = freqs[gait_mask][np.argmax(psd[gait_mask])]
            cadence = peak_freq * 60.0
            snr = np.max(psd[gait_mask]) / (np.mean(psd) + 1e-10)
        else:
            cadence = 0.0
            snr = 0.0

        # Compute harmonic ratio (2nd harmonic / 1st harmonic power)
        harmonic_ratio = 0.5
        if cadence > 0:
            fund_freq = cadence / 60.0
            second_harm = fund_freq * 2
            fund_mask = (freqs >= fund_freq * 0.8) & (freqs <= fund_freq * 1.2)
            harm_mask = (freqs >= second_harm * 0.8) & (freqs <= second_harm * 1.2)
            fund_power = np.sum(psd[fund_mask]) if np.any(fund_mask) else 1e-10
            harm_power = np.sum(psd[harm_mask]) if np.any(harm_mask) else 0
            harmonic_ratio = float(harm_power / (fund_power + 1e-10))

        sig_vector = np.zeros(SIGNATURE_LENGTH)
        chunk = SIGNATURE_LENGTH // 4

        gait_psd = psd[gait_mask] if np.any(gait_mask) else np.zeros(10)
        gait_psd_norm = gait_psd / (np.max(gait_psd) + 1e-10)
        if len(gait_psd_norm) > 1:
            f = interp1d(np.linspace(0, 1, len(gait_psd_norm)), gait_psd_norm, kind='linear')
            sig_vector[:chunk] = f(np.linspace(0, 1, chunk))

        ac = np.correlate(gait_signal, gait_signal, mode='full')
        ac = ac[len(ac)//2:]
        ac = ac[:chunk] / (ac[0] + 1e-10)
        sig_vector[chunk:2*chunk] = ac[:chunk]

        if n_components >= 2:
            cross_corr = np.correlate(components[:, 0], components[:, 1], mode='full')
            cross_corr = cross_corr[len(cross_corr)//2:]
            cc_norm = cross_corr[:chunk] / (np.max(np.abs(cross_corr)) + 1e-10)
            sig_vector[2*chunk:3*chunk] = cc_norm

        var_profile = np.var(velocity, axis=0)
        var_profile = var_profile / (np.max(var_profile) + 1e-10)
        if len(var_profile) > 1:
            f2 = interp1d(np.linspace(0, 1, len(var_profile)), var_profile, kind='linear')
            sig_vector[3*chunk:] = f2(np.linspace(0, 1, SIGNATURE_LENGTH - 3*chunk))

        norm = np.linalg.norm(sig_vector)
        if norm > 0:
            sig_vector = sig_vector / norm

        stride_regularity = 0.0
        if len(ac) > 20:
            peaks, _ = sig.find_peaks(ac, distance=10, height=0.1)
            if len(peaks) > 0:
                stride_regularity = float(ac[peaks[0]])

        quality = min(1.0, snr / 15.0)

        # Body attenuation: proxy for distance.  Higher CSI variance ⇒ closer body.
        # ESP32 normalises amplitudes to [0,1] per frame so inter-frame variance
        # is very small (~0.0001–0.001).  Log scale maps this to [0, 1].
        raw_var = float(np.mean(np.var(amplitude_matrix, axis=0)))
        # log-scale: var=0.0001→~0.0, var=0.0002→~0.3, var=0.0004→~0.6, var=0.001→~1.0
        body_attenuation = float(np.clip((np.log10(raw_var + 1e-6) + 4.0) / 1.0, 0.0, 1.0))

        return {
            'signature': sig_vector.tolist(),
            'cadence_spm': round(float(cadence), 1),
            'stride_regularity': round(float(stride_regularity), 3),
            'signal_quality': round(float(quality), 3),
            'harmonic_ratio': round(float(harmonic_ratio), 4),
            'body_attenuation': round(float(body_attenuation), 4),
        }


# ═════════════════════════════════════════════════════════════════════════════
# SIGNATURE MATCHING
# ═════════════════════════════════════════════════════════════════════════════

class SignatureMatcher:
    @staticmethod
    def cosine_similarity(a, b):
        dot = np.dot(a, b)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0

    @staticmethod
    def dtw_distance(a, b, max_warp=DTW_MAX_WARP):
        n, m = len(a), len(b)
        dtw_matrix = np.full((n+1, m+1), np.inf)
        dtw_matrix[0, 0] = 0.0
        for i in range(1, n+1):
            for j in range(max(1, i-max_warp), min(m, i+max_warp)+1):
                cost = abs(float(a[i-1]) - float(b[j-1]))
                dtw_matrix[i, j] = cost + min(dtw_matrix[i-1, j], dtw_matrix[i, j-1], dtw_matrix[i-1, j-1])
        return float(dtw_matrix[n, m])

    @classmethod
    def match(cls, new_sig, stored_sig):
        a, b = np.array(new_sig), np.array(stored_sig)
        cos_sim = cls.cosine_similarity(a, b)
        seg_len = min(32, len(a)//4)
        if seg_len > 4:
            dtw_scores = []
            for start in range(0, len(a) - seg_len, seg_len):
                d = cls.dtw_distance(a[start:start+seg_len], b[start:start+seg_len])
                dtw_scores.append(1.0 / (1.0 + d))
            dtw_avg = np.mean(dtw_scores) if dtw_scores else 0.0
        else:
            dtw_avg = cos_sim
        return round(float(0.65 * cos_sim + 0.35 * dtw_avg), 4)


# ═════════════════════════════════════════════════════════════════════════════
# SIMULATED CSI SOURCE
# ═════════════════════════════════════════════════════════════════════════════

class SimulatedCSISource:
    """
    Generates realistic synthetic CSI data with simulated humans and animals.
    Each entity has unique biometrics and movement patterns.
    """

    def __init__(self, n_people=3, seed=None):
        if seed is not None:
            np.random.seed(seed)
        self.people = []
        self.n_subcarriers = N_SUBCARRIERS
        self.sample_rate = CSI_SAMPLE_RATE

        # Create distinct simulated entities (mix of humans and animals)
        entities = [
            # Humans with sex-differentiated biometrics
            {'id': 'Entity_A', 'type': 'human', 'sex': 'male',
             'heart_rate': np.random.uniform(62, 78), 'resp_rate': np.random.uniform(13, 18),
             'gait_cadence': np.random.uniform(95, 115), 'body_attenuation': np.random.uniform(0.55, 0.80),
             'gait_asymmetry': np.random.uniform(0.88, 0.98)},
            {'id': 'Entity_B', 'type': 'human', 'sex': 'female',
             'heart_rate': np.random.uniform(68, 88), 'resp_rate': np.random.uniform(14, 20),
             'gait_cadence': np.random.uniform(108, 128), 'body_attenuation': np.random.uniform(0.35, 0.55),
             'gait_asymmetry': np.random.uniform(0.90, 0.99)},
            {'id': 'Entity_C', 'type': 'human', 'sex': 'male',
             'heart_rate': np.random.uniform(58, 72), 'resp_rate': np.random.uniform(12, 16),
             'gait_cadence': np.random.uniform(100, 118), 'body_attenuation': np.random.uniform(0.60, 0.85),
             'gait_asymmetry': np.random.uniform(0.85, 0.95)},
            # Dog
            {'id': 'Entity_D', 'type': 'dog', 'sex': 'unknown',
             'heart_rate': np.random.uniform(80, 130), 'resp_rate': np.random.uniform(18, 28),
             'gait_cadence': np.random.uniform(170, 230), 'body_attenuation': np.random.uniform(0.15, 0.35),
             'gait_asymmetry': np.random.uniform(0.70, 0.85)},
            # Cat
            {'id': 'Entity_E', 'type': 'cat', 'sex': 'unknown',
             'heart_rate': np.random.uniform(140, 200), 'resp_rate': np.random.uniform(20, 30),
             'gait_cadence': np.random.uniform(190, 260), 'body_attenuation': np.random.uniform(0.08, 0.20),
             'gait_asymmetry': np.random.uniform(0.65, 0.80)},
        ]

        # Fixed initial directions ensure the demo reliably shows all three states.
        # Majority approaching so the combined Doppler is clearly positive at startup.
        _initial_directions = [1, 1, -1, 1, 0]  # 3 approaching, 1 receding, 1 stationary

        for i, ent in enumerate(entities[:max(n_people, 5)]):
            ent['heart_variability'] = np.random.uniform(0.02, 0.08)
            ent['stride_length'] = np.random.uniform(0.6, 0.85) if ent['type'] == 'human' else np.random.uniform(0.2, 0.5)
            ent['subcarrier_profile'] = np.random.dirichlet(np.ones(N_SUBCARRIERS) * 2)
            ent['phase_offset'] = np.random.uniform(0, 2 * np.pi)
            ent['active'] = True
            ent['position'] = np.random.uniform(-5, 5, size=2)
            # Movement direction: +1 approaching, -1 receding, 0 stationary
            ent['move_direction'] = _initial_directions[i] if i < len(_initial_directions) else np.random.choice([-1, 0, 1])
            ent['radial_speed'] = np.random.uniform(0.3, 1.8) if ent['type'] == 'human' else np.random.uniform(0.5, 3.0)
            ent['accumulated_phase'] = np.zeros(N_SUBCARRIERS)  # persistent phase for Doppler
            self.people.append(ent)

        self._time = 0.0
        self._direction_change_timer = 0
        self._entity_idx = 0  # round-robin index for per-entity profiling

    def toggle_person(self, idx):
        if idx < len(self.people):
            self.people[idx]['active'] = not self.people[idx]['active']

    def generate_frames(self, n_frames):
        """
        Returns frames focused on a single entity (round-robin rotation).

        Processing each entity separately lets the signature extractor pull out
        that entity's distinctive biometrics (heart rate, gait cadence, body mass)
        rather than a blended average, which produces distinct per-entity profiles.
        """
        dt = 1.0 / self.sample_rate

        # ── Species-aware direction updates ───────────────────────────────────
        self._direction_change_timer += n_frames
        if self._direction_change_timer > self.sample_rate * 10:
            self._direction_change_timer = 0
            for p in self.people:
                species = p.get('type', 'human')
                if species == 'dog':
                    # Dogs are always moving — they pace, fetch, roam
                    if np.random.random() < 0.75:
                        p['move_direction'] = np.random.choice([-1, 1])
                elif species == 'cat':
                    # Cats are mostly stationary; occasional short bursts
                    if np.random.random() < 0.35:
                        p['move_direction'] = np.random.choice([-1, 0, 0, 0, 1])
                else:
                    # Humans: walking around, sometimes standing still
                    if np.random.random() < 0.35:
                        p['move_direction'] = np.random.choice([-1, -1, 0, 1, 1])
            # Keep at least one approaching entity so the demo always shows the banner
            if not any(p['move_direction'] == 1 and p['active'] for p in self.people):
                active = [p for p in self.people if p['active']]
                if active:
                    active[0]['move_direction'] = 1

        # ── Select current entity (round-robin) ───────────────────────────────
        active_entities = [p for p in self.people if p['active']]
        if not active_entities:
            # No entities: return low-level noise
            frames = [
                CSIFrame(
                    timestamp=self._time + i * dt,
                    amplitudes=np.abs(np.random.normal(0.1, 0.005, self.n_subcarriers)),
                    phases=np.random.uniform(-0.1, 0.1, self.n_subcarriers),
                )
                for i in range(n_frames)
            ]
            self._time += n_frames * dt
            return frames

        entity = active_entities[self._entity_idx % len(active_entities)]
        self._entity_idx = (self._entity_idx + 1) % len(active_entities)

        # ── Generate frames for this entity only ──────────────────────────────
        frames = []
        p = entity
        for _ in range(n_frames):
            t = self._time
            amps = np.ones(self.n_subcarriers) * 0.1

            hr_freq = p['heart_rate'] / 60.0
            hrv = p['heart_variability'] * np.sin(2 * np.pi * 0.1 * t)
            hb = p['body_attenuation'] * 0.02 * np.sin(2 * np.pi * (hr_freq + hrv) * t + p['phase_offset'])
            hb += p['body_attenuation'] * 0.008 * np.sin(2 * np.pi * 2 * hr_freq * t + p['phase_offset'])

            resp_freq = p['resp_rate'] / 60.0
            resp = p['body_attenuation'] * 0.05 * np.sin(2 * np.pi * resp_freq * t)

            gait_freq = p['gait_cadence'] / 60.0
            gait = p['body_attenuation'] * 0.15 * (
                np.sin(2 * np.pi * gait_freq * t) +
                p['gait_asymmetry'] * 0.3 * np.sin(2 * np.pi * 2 * gait_freq * t + 0.5)
            )
            gait += p['body_attenuation'] * 0.04 * np.sin(2 * np.pi * 2 * gait_freq * t + np.pi / 4)

            person_signal = (hb + resp + gait) * p['subcarrier_profile'] * self.n_subcarriers
            amps += person_signal

            # Accumulate Doppler phase shift so detect_direction sees a consistent trend
            doppler_base = 0.1 * gait_freq * np.cos(2 * np.pi * gait_freq * t)
            direction_drift = p['move_direction'] * p['radial_speed'] * 2 * np.pi / WAVELENGTH
            p['accumulated_phase'] += (doppler_base + direction_drift * dt) * p['subcarrier_profile'] * 5
            phases = p['accumulated_phase'] + np.random.normal(0, 0.02, self.n_subcarriers)

            amps += np.random.normal(0, 0.005, self.n_subcarriers)

            frames.append(CSIFrame(timestamp=t, amplitudes=np.abs(amps), phases=phases))
            self._time += dt

        return frames


# ═════════════════════════════════════════════════════════════════════════════
# PROFILE STORAGE
# ═════════════════════════════════════════════════════════════════════════════

class ProfileStore:
    def __init__(self, filepath='profiles.json'):
        self.filepath = filepath
        self._lock = threading.Lock()
        self.profiles = self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.profiles, f, indent=2, default=str)

    def _generate_id(self, signature):
        sig_bytes = np.array(signature).tobytes()
        return hashlib.sha256(sig_bytes).hexdigest()[:12]

    def add_or_update(self, sig_type, signature, metadata, match_id=None, confidence=0.0):
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            if match_id and match_id in self.profiles:
                profile = self.profiles[match_id]
                profile['last_seen'] = now
                profile['detection_count'] += 1
                profile['confidence_history'].append(confidence)
                profile['confidence_history'] = profile['confidence_history'][-50:]
                profile['avg_confidence'] = round(np.mean(profile['confidence_history']), 4)
                old_sig = np.array(profile['signature'])
                new_sig = np.array(signature)
                alpha = 0.15
                updated = (1 - alpha) * old_sig + alpha * new_sig
                updated = updated / (np.linalg.norm(updated) + 1e-10)
                profile['signature'] = updated.tolist()
                for k, v in metadata.items():
                    if k != 'signature':
                        profile['metadata'][k] = v
                self._save()
                return profile
            else:
                profile_id = self._generate_id(signature)
                profile = {
                    'id': profile_id, 'nickname': None, 'sig_type': sig_type,
                    'signature': signature, 'first_seen': now, 'last_seen': now,
                    'detection_count': 1,
                    'confidence_history': [confidence] if confidence > 0 else [],
                    'avg_confidence': confidence, 'metadata': metadata, 'tagged': False,
                }
                self.profiles[profile_id] = profile
                self._save()
                return profile

    def tag_profile(self, profile_id, nickname):
        with self._lock:
            if profile_id in self.profiles:
                self.profiles[profile_id]['nickname'] = nickname
                self.profiles[profile_id]['tagged'] = True
                self._save()
                return self.profiles[profile_id]
            return None

    def delete_profile(self, profile_id):
        with self._lock:
            if profile_id in self.profiles:
                profile = self.profiles[profile_id]
                sig_len = len(profile.get('signature', []))
                profile['signature'] = np.random.random(sig_len).tolist()
                profile['metadata'] = {}
                profile['nickname'] = None
                profile['confidence_history'] = []
                del self.profiles[profile_id]
                self._save()
                self._save()
                return True
            return False

    def get_all(self):
        with self._lock:
            raw = {}
            for pid, p in self.profiles.items():
                entry = {k: v for k, v in p.items() if k != 'signature'}
                entry['has_signature'] = len(p.get('signature', [])) > 0
                entry['signature_strength'] = float(np.linalg.norm(p.get('signature', [])))
                if entry.get('metadata', {}).get('species') != 'human':
                    entry.pop('device_candidates', None)
                raw[pid] = entry

            # ── Coalesce heartbeat + gait profiles that represent the same target ──
            # Criteria: same species, last_seen within 5 min, distance within 3m,
            # no conflicting sex estimation.
            hb_ids  = [pid for pid, e in raw.items() if e['sig_type'] == 'heartbeat']
            gait_ids = [pid for pid, e in raw.items() if e['sig_type'] == 'gait']

            COALESCE_TIME_S  = 300   # 5 minutes
            COALESCE_DIST_M  = 3.0   # metres

            def _last_seen_ts(entry):
                try:
                    return datetime.fromisoformat(entry['last_seen']).timestamp()
                except Exception:
                    return 0.0

            def _dist(entry):
                m = entry.get('metadata') or {}
                return m.get('distance_m') or (
                    max(0.5, (1.0 - m['body_attenuation']) * 8.0 + 0.5)
                    if m.get('body_attenuation') else None
                )

            def _sex_compatible(a, b):
                sa = (a.get('metadata') or {}).get('sex_estimation', 'indeterminate')
                sb = (b.get('metadata') or {}).get('sex_estimation', 'indeterminate')
                return sa == 'indeterminate' or sb == 'indeterminate' or sa == sb

            # Score candidate pairs; keep best non-overlapping set (greedy)
            candidates = []
            for hid in hb_ids:
                for gid in gait_ids:
                    he, ge = raw[hid], raw[gid]
                    hm = he.get('metadata') or {}
                    gm = ge.get('metadata') or {}
                    if hm.get('species') != gm.get('species'):
                        continue
                    dt = abs(_last_seen_ts(he) - _last_seen_ts(ge))
                    if dt > COALESCE_TIME_S:
                        continue
                    hd, gd = _dist(he), _dist(ge)
                    if hd is not None and gd is not None and abs(hd - gd) > COALESCE_DIST_M:
                        continue
                    if not _sex_compatible(he, ge):
                        continue
                    time_score = 1.0 - dt / COALESCE_TIME_S
                    dist_score = (1.0 - abs((hd or 4.0) - (gd or 4.0)) / COALESCE_DIST_M) if (hd and gd) else 0.5
                    score = (time_score + dist_score) / 2.0
                    candidates.append((score, hid, gid))

            candidates.sort(reverse=True)
            merged_hb, merged_gait = set(), set()
            merged_pairs = []
            for score, hid, gid in candidates:
                if hid in merged_hb or gid in merged_gait:
                    continue
                merged_hb.add(hid)
                merged_gait.add(gid)
                merged_pairs.append((hid, gid))

            # Build result: combined entries first, then unmatched
            result = []
            for hid, gid in merged_pairs:
                he, ge = raw[hid], raw[gid]
                hm = dict(he.get('metadata') or {})
                gm = dict(ge.get('metadata') or {})
                # Pick the more confident species/sex classification
                primary_meta = hm if hm.get('species_confidence', 0) >= gm.get('species_confidence', 0) else gm
                merged_meta = {**primary_meta}
                # Always include both biometric fields
                for k in ('bpm', 'respiratory_rate'):
                    if k in hm:
                        merged_meta[k] = hm[k]
                for k in ('cadence_spm', 'stride_regularity', 'harmonic_ratio'):
                    if k in gm:
                        merged_meta[k] = gm[k]
                merged_meta['signal_quality'] = max(
                    hm.get('signal_quality', 0), gm.get('signal_quality', 0)
                )
                merged_meta['distance_m'] = (
                    (_dist(he) or 0) + (_dist(ge) or 0)
                ) / 2.0 if _dist(he) and _dist(ge) else (_dist(he) or _dist(ge))

                # Prefer the tagged/nicknamed entry as primary
                primary, secondary = (he, ge) if (he.get('tagged') or not ge.get('tagged')) else (ge, he)
                combined = {
                    'id': primary['id'],
                    'nickname': primary.get('nickname') or secondary.get('nickname'),
                    'sig_type': 'combined',
                    'first_seen': min(he.get('first_seen',''), ge.get('first_seen','')),
                    'last_seen': max(he.get('last_seen',''), ge.get('last_seen','')),
                    'detection_count': he.get('detection_count', 0) + ge.get('detection_count', 0),
                    'avg_confidence': (he.get('avg_confidence', 0) + ge.get('avg_confidence', 0)) / 2.0,
                    'confidence_history': primary.get('confidence_history', []),
                    'tagged': primary.get('tagged', False) or secondary.get('tagged', False),
                    'metadata': merged_meta,
                    'has_signature': True,
                    'signature_strength': max(he.get('signature_strength', 0), ge.get('signature_strength', 0)),
                    'component_ids': [hid, gid],
                    'device_candidates': primary.get('device_candidates') or secondary.get('device_candidates'),
                }
                if combined.get('metadata', {}).get('species') != 'human':
                    combined.pop('device_candidates', None)
                result.append(combined)

            for pid, entry in raw.items():
                if pid not in merged_hb and pid not in merged_gait:
                    result.append(entry)

            return result

    def get_signatures_for_matching(self):
        with self._lock:
            return [
                {'id': pid, 'sig_type': p['sig_type'], 'signature': p['signature'], 'nickname': p.get('nickname')}
                for pid, p in self.profiles.items()
            ]

    def set_device_candidates(self, profile_id: str, candidates: list):
        """Store ranked device name candidates for a profile (humans only)."""
        with self._lock:
            if profile_id in self.profiles:
                species = self.profiles[profile_id].get('metadata', {}).get('species', '')
                if species != 'human':
                    # Clear any stale device data from non-human profiles
                    self.profiles[profile_id].pop('device_candidates', None)
                    self._save()
                    return
                self.profiles[profile_id]['device_candidates'] = candidates
                self._save()


# ═════════════════════════════════════════════════════════════════════════════
# DEVICE CORRELATOR
# ═════════════════════════════════════════════════════════════════════════════

class DeviceCorrelator:
    """
    Correlates CSI-detected subject profiles with nearby device display names.

    Each detection cycle, record which profiles were active and which devices
    were visible. This builds a co-presence count matrix:

        co_presence[profile_id][device_key] = N times seen together

    Correlation score = co_presence_count / total_profile_detections.

    Workflow:
      1. Detection cycle runs → call record_window(profile_ids, visible_devices)
      2. Operator sees a profile and suspects it's "Knibb High Football Rules"
         → call suggest(profile_id, "Knibb High Football Rules")
      3. System keeps accumulating co-presence data for that suggestion
      4. Once score ≥ AUTO_CONFIRM_THRESHOLD for ≥ AUTO_TAG_MIN_SIGHTINGS,
         check_auto_associations() returns it → server calls store.tag_profile()
    """

    # Confidence threshold to auto-confirm a suggested device→subject association
    AUTO_CONFIRM_THRESHOLD = 0.82
    # Minimum co-presence observations before auto-tag fires (avoids early false positives)
    AUTO_TAG_MIN_SIGHTINGS = 30

    def __init__(self, profile_store: 'ProfileStore'):
        self._store = profile_store
        self._lock = threading.Lock()
        # co_presence[profile_id][device_key] = int count
        self._co_presence: dict = defaultdict(lambda: defaultdict(int))
        # total detection windows per profile
        self._detection_counts: dict = defaultdict(int)
        # operator-suggested associations: profile_id → [display_name, ...]
        self._suggestions: dict = defaultdict(list)
        # already auto-tagged pairs so we don't re-fire
        self._confirmed: set = set()

    def record_window(self, profile_ids: list, visible_devices: dict):
        """
        Call once per detection cycle.

        profile_ids: list of profile IDs that matched in this window
        visible_devices: dict returned by DeviceScanner.get_visible()
        """
        with self._lock:
            self._total_windows = getattr(self, '_total_windows', 0) + 1
            # Track how many windows each device is visible in (for ambient detection)
            if not hasattr(self, '_device_window_counts'):
                self._device_window_counts = defaultdict(int)
            for dev_key in visible_devices:
                self._device_window_counts[dev_key] += 1

            for pid in profile_ids:
                self._detection_counts[pid] += 1
                for dev_key in visible_devices:
                    self._co_presence[pid][dev_key] += 1

    def suggest(self, profile_id: str, device_display_name: str):
        """
        Operator manually suggests that profile_id belongs to device_display_name.
        The system will auto-confirm once co-presence confidence is high enough.
        """
        with self._lock:
            if device_display_name not in self._suggestions[profile_id]:
                self._suggestions[profile_id].append(device_display_name)
                logger.info(
                    f"[DeviceCorrelator] Suggestion queued: "
                    f"profile {profile_id} → '{device_display_name}'"
                )

    def _selectivity(self, dev_key):
        """
        Returns a 0–1 factor: 1.0 for a device only seen with one profile,
        close to 0 for a device seen in every single window (ambient/stationary).
        """
        total_windows = getattr(self, '_total_windows', 1)
        dev_windows = getattr(self, '_device_window_counts', {}).get(dev_key, 0)
        if total_windows == 0:
            return 1.0
        # Fraction of windows where this device was visible
        ubiquity = dev_windows / total_windows
        # Penalize devices visible >80% of the time (ambient)
        if ubiquity > 0.8:
            return max(0.1, 1.0 - ubiquity)
        return 1.0

    def get_candidates(self, profile_id: str, visible_devices: dict) -> list:
        """
        Returns ranked device candidates for a profile:
          [{'display_name', 'device_key', 'score', 'sightings', 'suggested'}, ...]

        Score = co-presence fraction * selectivity. Suggested entries sort first.
        """
        with self._lock:
            total = self._detection_counts.get(profile_id, 0)
            if total == 0:
                return []

            results = []
            for dev_key, count in self._co_presence.get(profile_id, {}).items():
                dev_info = visible_devices.get(dev_key, {})
                display_name = dev_info.get('display_name', dev_key)
                suggested = display_name in self._suggestions.get(profile_id, [])
                raw_score = count / total
                score = raw_score * self._selectivity(dev_key)
                results.append({
                    'display_name': display_name,
                    'device_key': dev_key,
                    'score': round(score, 3),
                    'sightings': count,
                    'suggested': suggested,
                })

            results.sort(key=lambda x: (-int(x['suggested']), -x['score']))
            return results

    def check_auto_associations(self, visible_devices: dict) -> list:
        """
        Returns list of (profile_id, display_name, score) tuples that have
        accumulated enough co-presence evidence to be auto-confirmed.

        Call this each detection cycle; the server should then call
        store.tag_profile() for each returned pair.
        """
        to_tag = []
        with self._lock:
            for pid, suggestions in self._suggestions.items():
                total = self._detection_counts.get(pid, 0)
                if total < self.AUTO_TAG_MIN_SIGHTINGS:
                    continue
                for display_name in suggestions:
                    pair = (pid, display_name)
                    if pair in self._confirmed:
                        continue
                    # Find the device key(s) matching this display name
                    for dev_key, count in self._co_presence.get(pid, {}).items():
                        dev_info = visible_devices.get(dev_key, {})
                        if dev_info.get('display_name', '') == display_name:
                            score = (count / total) * self._selectivity(dev_key)
                            if score >= self.AUTO_CONFIRM_THRESHOLD:
                                to_tag.append((pid, display_name, round(score, 3)))
                                self._confirmed.add(pair)
                                logger.info(
                                    f"[DeviceCorrelator] Auto-confirmed: "
                                    f"profile {pid} → '{display_name}' "
                                    f"(score={score:.3f}, n={count}/{total})"
                                )
                            break
        return to_tag