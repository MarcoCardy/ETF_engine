# Task 7 brief — Chronos CLI and forecast-workflow verification

Implement Task 7 under TDD and Ponytail full.

## Files

- Modify `perpetual_engine/cli.py`.
- Modify `tests/test_chronos.py` only for focused CLI tests.
- Verify but do not change `requirements.txt` unless the frozen required entries are missing.
- Do not implement portfolio commands; Task 8 adds those.

## Commands

Add the lazy-loaded `chronos` group with exactly:

- `refresh --config PATH` -> `refresh_chronos_data(config)`;
- `forecast --config PATH --output PATH` -> `publish_forecast(config, output)`;
- `evaluate --config PATH --output PATH` -> `evaluate_chronos(config, output)`;
- `reconcile --config PATH --forecast-root PATH --output PATH` -> `reconcile_forecasts(config, forecast_root, output)`.

Use `Path` arguments, require the subcommand, import Chronos modules only inside the selected dispatch branch, and preserve every existing command/exit behavior. On success print only the returned ID/path and return `0`. Route malformed config, data, hash, path, archive, or model-input failures through the existing `Input error` boundary and return `2`; include `forecast_root` in the same path-aware handling. Do not catch programming errors broadly.

## TDD and verification

- Add `ChronosCliTests` that patch the four functions at their true module boundaries, assert exact `Path` arguments, exit `0`, and isolation from legacy handlers.
- Add at least one corrupt-input dispatch that prints `Input error` and returns `2` without a traceback.
- Capture RED before the parser recognizes `chronos`, then GREEN.
- Run focused CLI tests, all `tests.test_chronos`, and the complete suite.
- Run `.venv/Scripts/python.exe -m pip check` and confirm `requirements.txt` still includes `chronos-forecasting==2.3.1` and `transformers>=4.41,<5`; add no dependency.
- Run one cached, offline CPU smoke using the frozen Chronos revision, a synthetic `(4,24)` target, five past covariates and 12 future ECB values; assert one finite `(4,12,3)` output. No network/download is authorized.
- Produce `task-7-head`, `task-7-review.diff`, `task-7-report.md`, update the ledger to independent-review pending, and do not use Git.
