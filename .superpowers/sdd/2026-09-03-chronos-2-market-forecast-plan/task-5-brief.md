# Task 5 brief — Walk-forward value, covariate contribution, and volatility diagnostics

Implement Task 5 from the revised plan under TDD.

## Files

- Modify `perpetual_engine/chronos.py`.
- Modify `tests/test_chronos.py`.

## Required interfaces

- `pinball_loss(actual: np.ndarray, forecast: np.ndarray, quantile: float) -> np.ndarray`.
- `moving_block_interval(origin_differences: np.ndarray, config: ChronosConfig) -> tuple[float, float]`.
- `evaluation_variants(covariates: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]`.
- `evaluate_chronos(config_path: Path, output: Path, predictor: Callable | None = None) -> Path`.

## Binding evaluation contract

- Use exactly the last 36 origins with 12 realized months. For the frozen 240-month target these are `2022-06-30` through `2025-05-31`.
- Each item uses expanding target/covariate history ending at its origin. Operational variants never receive future covariates.
- Five covariates produce exactly 12 Chronos variants: target-only; five standalone additions; full; five full-minus-one. Add separate `ZERO_RETURN_BASELINE` and separately labelled `ORACLE_FUTURE_RATE_UPPER_BOUND` comparators; neither counts among the 12.
- The oracle alone receives the actual next 12 ECB values. It is an upper-bound diagnostic, never an implementable result.
- Batch all 36 origins once per model variant and release the batch before the next variant.
- Metrics use q50 MAE, mean pinball loss across q10/q50/q90, and inclusive q10-q90 coverage at horizons 1,3,6,12 and pooled ALL. Primary comparison is pooled pinball loss.
- Contribution output reports standalone and full-minus conditional improvements. Conditional classification uses paired per-origin moving-block bootstrap: block 6, 2,000 resamples, seed 42, percentile 95%; USEFUL iff lower bound >0, HARMFUL iff upper bound <0, otherwise INCONCLUSIVE.
- Never modify enabled covariates based on these results and never enumerate all subsets.

## Volatility diagnostic

- Reuse sample standard deviation of the 12 target returns known through each origin times `sqrt(12)`.
- Produce diagnostics only for the FULL model. For each target, calculate retrospective 1/3 and 2/3 thresholds from its 36 origin-known trailing volatilities using NumPy linear quantiles; store thresholds in the manifest.
- Output `origin,target,horizon,trailing_volatility_12m,volatility_tercile,q10,q50,q90,interval_width,actual,signed_error,absolute_error,interval_hit`.
- Terciles are descriptive only and do not enter model inputs or model selection.

## Atomic outputs

- `predictions.csv`: `variant,origin,target,forecast_month,horizon,actual,q10,q50,q90`.
- `metrics.csv`: `variant,target,horizon,mae_q50,mean_pinball_loss,interval_80_coverage`.
- `covariate_contribution.csv`: `covariate,comparison,horizon,loss_without,loss_with,improvement,ci_low,ci_high,classification`.
- `volatility_diagnostics.csv`: exact schema above.
- `manifest.json`: model/config/target/vintage hashes, origins, bootstrap settings, volatility thresholds, retrospective/oracle labels, and every output hash.
- Stage below the requested output parent, reject path escape/collision, and publish only after all bytes/hashes validate. An existing destination is reusable only when byte-identical.

## Scope and verification

Use a deterministic fake predictor in tests; no network and no real model load in the ordinary suite. Do not add prospective publication/reconciliation, scenario-sensitivity, CLI, portfolio, dependency, or allocation logic. Capture RED, run focused `ChronosEvaluationTests`, then the full suite once. Produce task-5 snapshots/diff/report and ledger review-pending status. Do not initialize Git.
