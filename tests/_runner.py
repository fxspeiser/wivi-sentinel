"""Shared no-pytest test runner: `python3 tests/test_x.py` runs every test_* in it."""

import sys


def run(namespace: dict) -> None:
    tests = [v for k, v in sorted(namespace.items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f'  PASS  {fn.__name__}')
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            failed += 1
            print(f'  FAIL  {fn.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - failed}/{len(tests)} passed')
    sys.exit(1 if failed else 0)
