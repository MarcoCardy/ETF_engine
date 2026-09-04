# Task 4 brief — ECB scenarios and offline Chronos adapter

Implement Task 4 from the revised plan under TDD.

## Files

- Create `perpetual_engine/chronos.py`.
- Modify `tests/test_chronos.py`.

## Required interfaces

- Immutable `ForecastRow` with one wide row per scenario/target/horizon and fields `q10`, `q50`, `q90`.
- `ecb_scenarios(last_rate: float, basis_points: int, length: int) -> dict[str, np.ndarray]`.
- `build_forecast_inputs(targets: MonthlyTable, covariates: MonthlyTable, scenarios: Mapping[str, np.ndarray]) -> list[dict[str, object]]`.
- `load_chronos_predictor(config: ChronosConfig) -> Callable`.
- `run_scenario_forecasts(config: ChronosConfig, predictor: Callable | None = None) -> tuple[ForecastRow, ...]`.

## Binding requirements

- V1 accepts exactly 12 horizons, quantiles `[0.1, 0.5, 0.9]`, and 100 basis points; scenario names are exactly `ECB_FLAT`, `ECB_DOWN_100BP`, `ECB_UP_100BP`.
- From last rate `r0`, flat stays fixed, down ends at `r0-1.0`, and up ends at `r0+1.0`, linearly over 12 months without a zero floor.
- Each scenario item contains target shape `(4, history)` ordered WORLD/MOMENTUM/QUALITY/TREND, five equal-length historical covariates ordered ECB/Treasury/Brent/BIS/CPI, and only a 12-value `ECB_DFR` future covariate.
- Align targets and covariates to their complete common history and never include a row after the forecast origin.
- Load only `amazon/chronos-2` revision `29ec3766d36d6f73f0696f85560a422f50e8498c` on CPU with `local_files_only=True`.
- Accept the installed Chronos tuple/list return shape at the adapter boundary, convert tensors to NumPy, require exactly one finite `(4,12,3)` result per scenario, and reject crossed quantiles.
- Produce exactly 144 wide rows sorted scenario, target, horizon, with real month ends for horizons 1..12.
- Fake-predictor tests are offline. Do not add evaluation, publication, reconciliation, volatility, sensitivity, portfolio, network calls, or dependencies.

## Verification and packaging

Capture RED before production code. Run focused `ChronosForecastTests`, then the full suite once. Snapshot files under `task-4-base` and `task-4-head`, produce `task-4-review.diff` and `task-4-report.md`, update ledger to review pending, and do not initialize Git.
