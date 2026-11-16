"""
Runtime Configuration
=====================
Persisted, validated, thread-safe settings for the Wi-Vi Sentinel engine.

Design notes:
  - Every detector is independently switchable. A disabled detector does no
    work and reports no value — it is never conflated with "measured zero".
  - The detection loop takes an immutable `snapshot()` once per window, so a
    toggle flipped mid-analysis can never produce a half-configured result.
  - Writes are atomic (tmp file + os.replace) so a crash mid-save cannot
    leave a truncated config behind.
"""

import json
import os
import threading
from dataclasses import dataclass, asdict, fields, replace
from typing import Any

# WIVI_DATA_DIR relocates all persisted state (config, profiles, enrolment),
# e.g. for tests or a throwaway demo instance. Defaults to <repo>/data.
DATA_DIR = os.environ.get('WIVI_DATA_DIR') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'
)
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')


@dataclass(frozen=True)
class SentinelConfig:
    """Immutable settings snapshot. Frozen so a captured snapshot stays stable."""

    # ── Detector switches ────────────────────────────────────────────────────
    heartbeat_enabled: bool = True
    gait_enabled: bool = True
    direction_detection_enabled: bool = True
    species_classification_enabled: bool = True
    sex_estimation_enabled: bool = True
    device_correlation_enabled: bool = True

    # ── Profile lifecycle ────────────────────────────────────────────────────
    # When False, known profiles still match and update, but unmatched
    # signatures no longer mint new profiles. Stops runaway profile growth.
    profile_creation_enabled: bool = True

    # ── Analysis pipeline v2 (engine/analysis.py) ────────────────────────────
    # The v2 measurements are always computed and shown beside the originals.
    # This switch decides whether the species/sex classifiers are *fed* the v2
    # rates when v2 judges them valid. Off by default: the original inputs
    # stay in charge until the operator has compared the two on real data.
    use_v2_measurements: bool = False
    # Identify walkers against the enrolled roster (inert until a model is trained).
    roster_identification_enabled: bool = True
    identity_min_confidence: float = 0.55
    identity_min_margin: float = 0.15

    # ── Matching / gating thresholds ─────────────────────────────────────────
    # These mirror MATCH_THRESHOLD / HIGH_CONFIDENCE in engine/csi_processor.py.
    # Duplicated deliberately: this module stays free of numpy/scipy so the
    # settings layer can be imported and tested on its own.
    match_threshold: float = 0.60
    high_confidence: float = 0.85
    min_signal_quality: float = 0.08
    scan_interval: float = 1.0
    max_history: int = 200

    # Toggles the operator may flip; ordered for UI rendering.
    DETECTOR_KEYS = (
        'heartbeat_enabled',
        'gait_enabled',
        'direction_detection_enabled',
        'species_classification_enabled',
        'sex_estimation_enabled',
        'device_correlation_enabled',
        'profile_creation_enabled',
        'use_v2_measurements',
        'roster_identification_enabled',
    )

    NUMERIC_BOUNDS = {
        'match_threshold':    (0.0, 1.0),
        'high_confidence':    (0.0, 1.0),
        'min_signal_quality': (0.0, 1.0),
        'scan_interval':      (0.1, 60.0),
        'max_history':        (10, 5000),
        'identity_min_confidence': (0.0, 1.0),
        'identity_min_margin':     (0.0, 1.0),
    }

    def to_dict(self) -> dict:
        return asdict(self)


class ConfigError(ValueError):
    """Raised when a submitted config update fails validation."""


class ConfigManager:
    """Loads, validates, persists and hands out immutable config snapshots."""

    def __init__(self, path: str = CONFIG_PATH):
        self._path = path
        self._lock = threading.RLock()
        self._config = SentinelConfig()
        self._field_types = {f.name: f.type for f in fields(SentinelConfig)}
        self.load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def load(self) -> SentinelConfig:
        """Read config from disk. A malformed file falls back to defaults."""
        with self._lock:
            if not os.path.exists(self._path):
                self.save()
                return self._config
            try:
                with open(self._path, 'r') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError, OSError):
                # Corrupt or unreadable — keep defaults rather than crash the engine.
                return self._config
            known = {k: v for k, v in data.items() if k in self._field_types}
            try:
                self._config = self._coerce(known, base=SentinelConfig())
            except ConfigError:
                self._config = SentinelConfig()
            return self._config

    def save(self) -> None:
        """Atomically write the current config to disk."""
        with self._lock:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = f'{self._path}.tmp'
            with open(tmp, 'w') as f:
                json.dump(self._config.to_dict(), f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)

    # ── Access ───────────────────────────────────────────────────────────────

    def snapshot(self) -> SentinelConfig:
        """Return the current immutable config. Safe to hold for a whole window."""
        with self._lock:
            return self._config

    def update(self, updates: dict) -> SentinelConfig:
        """Validate and apply a partial update, then persist. Raises ConfigError."""
        if not isinstance(updates, dict):
            raise ConfigError('Config update must be a JSON object')
        unknown = set(updates) - set(self._field_types)
        if unknown:
            raise ConfigError(f"Unknown config key(s): {', '.join(sorted(unknown))}")
        with self._lock:
            self._config = self._coerce(updates, base=self._config)
            self.save()
            return self._config

    # ── Validation ───────────────────────────────────────────────────────────

    def _coerce(self, updates: dict, base: SentinelConfig) -> SentinelConfig:
        """Type-check and range-check `updates`, returning a new config."""
        clean: dict[str, Any] = {}
        for key, value in updates.items():
            declared = self._field_types.get(key)
            if declared is None:
                continue
            if declared is bool or declared == 'bool':
                if not isinstance(value, bool):
                    raise ConfigError(f"'{key}' must be true or false")
                clean[key] = value
            elif declared is int or declared == 'int':
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"'{key}' must be a number")
                clean[key] = int(value)
            elif declared is float or declared == 'float':
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"'{key}' must be a number")
                clean[key] = float(value)
            else:
                clean[key] = value

            bounds = SentinelConfig.NUMERIC_BOUNDS.get(key)
            if bounds is not None:
                lo, hi = bounds
                if not (lo <= clean[key] <= hi):
                    raise ConfigError(f"'{key}' must be between {lo} and {hi}")

        return replace(base, **clean)


# Module-level singleton used by the server and engine.
config_manager = ConfigManager()
