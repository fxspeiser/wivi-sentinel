"""
Tests for the persisted runtime configuration.

Run with:  python3 -m pytest tests/ -q        (pip install pytest)
       or:  python3 tests/test_config.py      (no pytest needed)
"""

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import ConfigManager, ConfigError, SentinelConfig


def _mgr():
    """A ConfigManager backed by a throwaway file."""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    os.unlink(path)  # let the manager create it
    return ConfigManager(path=path), path


def test_defaults_enable_the_core_detectors():
    mgr, _ = _mgr()
    snap = mgr.snapshot()
    assert snap.heartbeat_enabled is True
    assert snap.gait_enabled is True
    assert snap.match_threshold == 0.60


def test_heartbeat_and_gait_toggle_independently():
    """The two detectors must never imply one another."""
    mgr, _ = _mgr()
    assert mgr.update({'heartbeat_enabled': False}).gait_enabled is True
    assert mgr.snapshot().heartbeat_enabled is False

    assert mgr.update({'gait_enabled': False}).heartbeat_enabled is False
    assert mgr.update({'heartbeat_enabled': True}).gait_enabled is False


def test_settings_persist_across_restart():
    mgr, path = _mgr()
    mgr.update({'gait_enabled': False, 'match_threshold': 0.42})

    reloaded = ConfigManager(path=path).snapshot()
    assert reloaded.gait_enabled is False
    assert reloaded.match_threshold == 0.42


def test_rejects_wrong_types_unknown_keys_and_out_of_range():
    mgr, _ = _mgr()
    for bad in ({'heartbeat_enabled': 'yes'},
                {'heartbeat_enabled': 1},
                {'match_threshold': 5.0},
                {'match_threshold': -0.1},
                {'scan_interval': 0.0},
                {'not_a_setting': True}):
        try:
            mgr.update(bad)
        except ConfigError:
            pass
        else:
            raise AssertionError(f'accepted invalid update: {bad}')

    # A rejected update must leave the previous config untouched.
    assert mgr.snapshot().match_threshold == 0.60


def test_rejected_update_is_not_written_to_disk():
    mgr, path = _mgr()
    mgr.update({'match_threshold': 0.5})
    try:
        mgr.update({'match_threshold': 99})
    except ConfigError:
        pass
    with open(path) as f:
        assert json.load(f)['match_threshold'] == 0.5


def test_corrupt_file_falls_back_to_defaults():
    mgr, path = _mgr()
    with open(path, 'w') as f:
        f.write('{ not valid json')
    assert ConfigManager(path=path).snapshot().heartbeat_enabled is True


def test_snapshot_is_immutable():
    """The loop holds a snapshot for a whole window; it must not be mutable."""
    mgr, _ = _mgr()
    snap = mgr.snapshot()
    try:
        snap.heartbeat_enabled = False
    except Exception:
        pass
    else:
        raise AssertionError('snapshot was mutable')


def test_snapshot_is_stable_while_config_changes():
    mgr, _ = _mgr()
    held = mgr.snapshot()
    mgr.update({'heartbeat_enabled': False})
    assert held.heartbeat_enabled is True        # captured window unaffected
    assert mgr.snapshot().heartbeat_enabled is False


def test_concurrent_updates_do_not_corrupt_the_file():
    mgr, path = _mgr()

    def flip(n):
        for _ in range(20):
            mgr.update({'heartbeat_enabled': bool(n % 2)})

    threads = [threading.Thread(target=flip, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(path) as f:
        data = json.load(f)                      # must still be valid JSON
    assert set(data) == {f for f in SentinelConfig().to_dict()}


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f'  PASS  {fn.__name__}')
        except Exception as e:
            failed += 1
            print(f'  FAIL  {fn.__name__}: {e}')
    print(f'\n{len(tests) - failed}/{len(tests)} passed')
    sys.exit(1 if failed else 0)
