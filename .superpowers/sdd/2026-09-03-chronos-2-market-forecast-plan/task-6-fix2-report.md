# Task 6 fix round 2 report

Status: five Important re-review findings addressed; ready for independent re-review.

## Root-cause fixes

1. Publication is serialized per output root with one in-process mutex plus a Windows `msvcrt` byte lock. `_atomic_snapshot()` now returns `(path, created)` from its own destination check. Automatic rollback runs only for `created=True`, under the same lock, after resolving containment and matching every owned byte.
2. The public interface is exactly `reconcile_forecasts(config_path, forecast_root, output)`. New-forecast anchoring uses a private in-memory proof containing the just-created ID and manifest hash; public callers cannot supply a bypass keyword.
3. A prior monitoring anchor is accepted only when its resolved root is a real direct child of the expected output root. Symlink/junction redirection outside that root fails closed.
4. Vintage manifest and normalized covariate CSV are each read once, then hashed and parsed from those exact bytes. The current pointer verifies the same manifest bytes passed into vintage validation.
5. Forecast and monitoring pre-commit callbacks read the target CSV/manifest bundle twice, require byte stability and the expected hashes, and execute immediately before reuse or `Path.replace()`.

## TDD evidence

- RED: seven focused regressions reproduced all five findings (six assertion failures plus the expected missing-lock import before implementation).
- GREEN focused: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosRefreshTests tests.test_chronos.ChronosMonitoringTests` — 31/31 passed in 13.100s.
- GREEN full: `.venv\Scripts\python.exe -m unittest discover` — 283/283 passed in 82.415s.
- Syntax: `.venv\Scripts\python.exe -m py_compile perpetual_engine\chronos.py perpetual_engine\chronos_data.py tests\test_chronos.py` — passed.

## Review package

- Base: `task-6-fix2-base/`
- Head: `task-6-fix2-head/`
- Diff: `task-6-fix2-review.diff`
- Diff SHA-256: `8B1A73C5FB10A78AC8A0CE772BE8A4434996386AD96B2D541903B02B52ECF836`

Independent re-review is required before Task 6 is marked complete.
