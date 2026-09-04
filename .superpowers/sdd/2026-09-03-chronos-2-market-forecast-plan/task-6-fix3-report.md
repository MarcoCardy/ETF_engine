# Task 6 fix round 3 report

Status: the two concrete path-escape findings are fixed; the bootstrap finding was evaluated against the actual trust boundary; ready for independent re-review.

## Root-cause fixes

1. `.publication.lock` is opened without writing content, its opened path is resolved and required to remain the same-named direct child of the resolved output root, and only then is an empty lock initialized and byte-locked. A symlink to an external file now fails without changing the external bytes.
2. `current_manifest.json` is resolved through `_contained_file()` before it is read, so a pointer symlink outside `data_root` fails closed.
3. Removed `_BOOTSTRAP_TOKEN` and the token field/check from `_BootstrapProof`; they implied authentication that Python module globals cannot provide. The supported public interface remains exactly `reconcile_forecasts(config_path, forecast_root, output)` and rejects bypass keywords.

## Bootstrap trust-boundary evaluation

The reviewer demonstrated a caller importing underscore globals while also rewriting archived manifests. Such a caller already has arbitrary write access to forecast and monitoring storage and can forge a monitoring directory directly; a module-global token, a closure discoverable through Python introspection, or another underscore helper cannot create a security boundary against it. Moving the same state into a closure would add refactoring without changing that result. Therefore the implementation keeps the internal ID+manifest-hash transport, removes the false-secret token, and treats module internals plus output storage as trusted application code. No HMAC, DPAPI, dependency, or unsupported public bypass was added.

## TDD evidence

- RED: 2/2 focused regressions failed for the expected reasons; the external pointer was accepted and public publication wrote `b"0"` through the escaped lock link.
- GREEN path regressions plus public-interface guard: 3/3 passed in 0.687s.
- GREEN focused Refresh+Monitoring: 33/33 passed in 5.235s.
- GREEN full: `.venv\Scripts\python.exe -m unittest discover` — 285/285 passed in 82.834s.
- Syntax: `.venv\Scripts\python.exe -m py_compile perpetual_engine\chronos.py perpetual_engine\chronos_data.py tests\test_chronos.py` — passed.

## Review package

- Base: `task-6-fix3-base/`
- Head: `task-6-fix3-head/`
- Diff: `task-6-fix3-review.diff`
- Diff SHA-256: `1C2F791FB50F8D056DA0DE39F31A337B6B1655FEA7374DBFE30456C045DB1C49`

Independent re-review is required before Task 6 is marked complete.
