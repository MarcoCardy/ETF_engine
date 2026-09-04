# Task 6 fix round 1 report

Status: seven Important findings and two local minor findings addressed; ready for independent re-review.

## Root-cause fixes

1. Publication now passes the exact in-memory target and covariate tables from the hash-bound snapshot into the single scenario-forecast call; later reloads only validate that current input identity did not change.
2. Forecast, sensitivity, and manifest files are resolved, read once into bytes, hashed from those bytes, and parsed/compared from the same bytes.
3. Forecast root, direct archive children, internal files, staging paths, and monitoring anchors are resolved and confined to their requested roots; link/junction escapes fail closed.
4. Every pre-existing forecast manifest hash, including older forecasts during a new publication, must appear with its forecast ID in a fully validated prior monitoring snapshot before its `issued_at` is trusted.
5. Added `load_covariate_vintage()` and validate archived ECB scenario curves against the exact ECB value at the archived origin in the archived `vintage_id`.
6. Target hashes, and current vintage identity for forecasts, are rechecked by a callback inside the atomic publisher immediately before reuse or `Path.replace()`.
7. If automatic reconciliation fails, publication removes only a newly created forecast whose current bytes still exactly match the just-published candidate; pre-existing forecasts are never removed.
8. Archived model metadata must equal the frozen V1 model/revision/package-version/device fields, and CSV rows with extra fields are rejected.

## TDD evidence

- RED reproductions covered poisoned reload tables, hash/parse byte swapping, forecast-root/archive/file links, unanchored issue times, wrong vintage ECB base, both atomic mutation windows, and partial forecast publication after monitoring failure.
- Additional RED covered an altered older forecast while publishing a new ID; anchoring now applies to every pre-existing archive.
- Minor RED covered arbitrary model device metadata; the same test reaches and verifies rejection of extra CSV fields after restoring metadata.
- GREEN focused: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosMonitoringTests -v` — 17/17 passed in 2.275s.
- Adjacent Chronos suites: 34/34 passed in 16.516s.
- Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 276/276 passed in 80.769s.
- Syntax check: `.venv\Scripts\python.exe -m py_compile perpetual_engine\chronos.py perpetual_engine\chronos_data.py tests\test_chronos.py` — passed.

## Review package

- Base: `task-6-fix1-base/`
- Head: `task-6-fix1-head/`
- Diff: `task-6-fix1-review.diff`

Independent re-review is required before Task 6 is marked complete.
