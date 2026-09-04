# Task 6 brief — Immutable forecasts and prospective reconciliation

Implement Task 6 from the approved plan under TDD and Ponytail full.

## Files

- Modify `perpetual_engine/chronos.py`.
- Modify `tests/test_chronos.py`.
- Do not implement CLI or portfolio work.

## Required interfaces

- `IssuedForecastRow` and `ReconciledRow` frozen dataclasses with the exact plan fields.
- `forecast_id(config, target_hash, vintage_id, origin) -> str`.
- `publish_forecast(config_path, output_root, *, predictor=None, issued_at=None) -> str`.
- `reconcile_rows(forecasts, actuals) -> tuple[ReconciledRow, ...]`.
- `reconcile_forecasts(config_path, forecast_root, output) -> str`.

## Binding forecast contract

- Use `load_target_snapshot()` so the table and target hashes describe the same bytes; reject target mutations before publication.
- Load the validated frozen covariate vintage, run `run_scenario_forecasts()` exactly once, and publish exactly 144 rows with schema `scenario,target,forecast_month,horizon,q10,q50,q90`.
- Derive `scenario_sensitivity.csv` from those same rows without a second model call. Include only down/up versus flat with schema `target,forecast_month,horizon,scenario,q50,flat_q50,q50_delta,interval_width,flat_interval_width,interval_width_delta`.
- Forecast identity binds model revision, config hash, target hash, vintage ID, origin, and frozen relative scenario shapes. It must be deterministic and independent of issuance time.
- `output_root` resolves below the project root. Publish forecast directories as `output_root/forecasts/<forecast_id>` through staging below `output_root/.staging`; never overwrite.
- Existing forecast ID: validate the archived manifest and generated hashes. Reuse only if the newly computed immutable bytes are identical apart from the preserved original `issued_at`; otherwise fail closed. A repeat must never rewrite the original issue time.
- `issued_at` must be timezone-aware UTC; default to current UTC. The manifest contains forecast ID, first issue time, origin, model/package/device, actual scenario definitions, research/retrospective labels, config hash, exact target and target-manifest hashes, vintage ID, and generated hashes.
- After a successful forecast publication or exact reuse, automatically reconcile all archived forecasts to `output_root/monitoring`.

## Binding reconciliation contract

- Read only direct 64-lowercase-hex children of `forecast_root`; reject unexpected children, malformed manifests, hash mismatches, duplicate/ambiguous forecast rows, invalid quantiles, or identity/path inconsistencies.
- Match each `(forecast_month, target)` uniquely against the latest manifest-validated target snapshot.
- Realized schema: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90,actual,signed_error,absolute_error,squared_error,interval_hit`.
- Pending schema: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90`.
- `signed_error = actual - q50`; interval hit is inclusive. Future/unavailable months are pending only and never enter metrics.
- Live metrics schema: `scenario,target,horizon,count,bias,mae,rmse,interval_80_coverage`. Aggregate every non-empty exact scenario/target/horizon group and pooled rows required by the plan; no empty group row.
- Stable sort rows by forecast ID/scenario/target/horizon (with dates where needed).
- Monitoring identity binds exact target CSV hash, exact target manifest hash, and sorted included forecast-manifest hashes. Publish append-only snapshots as `output/<monitoring_id>` through staging; identical snapshots reuse byte-for-byte, changed actual data creates a new ID and preserves old snapshots.
- Monitoring manifest label is `PROSPECTIVE_TRACK_RECORD` and records all generated hashes.

## Failure and verification

- All paths resolve below project root and requested roots; fail before partial publication on escape/collision/corruption.
- Use deterministic fake predictors and injected UTC times; no network or real model in ordinary tests.
- TDD must cover deterministic ID, first issue-time preservation, collision/corruption, one inference call for sensitivity, formulas, pending rows, live metrics, new monitoring ID after actuals, and path escape.
- Run focused `ChronosMonitoringTests`, then the full suite once.
- Produce `task-6-head`, `task-6-review.diff`, `task-6-report.md`, update the ledger to independent-review pending, and do not use Git.
