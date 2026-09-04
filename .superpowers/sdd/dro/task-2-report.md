# DRO Task 2 report

## RED

- Added focused selector/accounting tests for 12-1 ranking, the 10-month trend filter, positive absolute return, frozen-order ties, cash fallback, next-period return timing, two-leg 10 bps costs, and partial-month metric exclusion.
- `\.venv\Scripts\python.exe -m pytest tests\test_dro.py -q` initially failed during collection with the expected `ImportError`: `calculate_monthly_decision` did not exist.

## GREEN

- Added immutable diagnostics, monthly decision, and complete-month return-summary records.
- `calculate_monthly_decision` validates every supplied observation with `validate_monthly_observation`, ranks `t-12` through `t-1`, filters by `t-9:t` mean and positive return, resolves ties through frozen candidate order, applies only the next-month return, and charges 10 bps per entry/exit leg.
- `summarize_strategy_returns` excludes `is_partial=True` decisions.
- Focused suite: `13 passed in 0.14s`.
- Full suite: `206 passed in 78.01s`.

## Files

- Modified `perpetual_engine/dro.py`.
- Modified `tests/test_dro.py`.
- Added this report.

## Concerns / deferred work

- No historical data, cash-rate series, downloads, or published DRO study outputs were added; those remain Tasks 3 and 4.
- Cash return is an explicit accounting input until Task 3 freezes its required rate history.

## Review round 1 — RED

- Added tests for stale and future `available_at`, a 2026 provenance retrieval of historically available data, rejection of a `TRADABLE_ETF` source in ranking history, and omission of `cash_return`.
- The focused test run failed as intended because `MonthlyTotalReturnObservation` had no `available_at` field.

## Review round 1 — GREEN

- `available_at` is now an explicit date distinct from provenance `retrieved_at`; validation enforces `observation_date <= available_at <= signal_as_of` and the configured staleness bound.
- Ranking inputs must be declared `CONDITIONAL_INDEX_PROXY` sources. `cash_return` is keyword-required; synthetic zeroes are explicit in tests only.
- Focused suite: `17 passed in 0.11s`.
- Full suite: `210 passed in 72.51s`.

## Review round 2 — RED

- Added a delayed-lookback case: a 2020-12-31 observation, available on 2021-01-15, is eligible for the 2021-01-31 decision.
- The focused suite failed as expected because indexing used each observation's own month-end as the availability cutoff.

## Review round 2 — GREEN

- Lookback availability is now checked against the decision `signal_month`; staleness uses `min(observation.month_end, signal_as_of)` and rejects future-dated or stale observations.
- Execution-period return data is validated against its execution month, preserving no-lookahead accounting.
- Focused suite: `18 passed in 0.16s`.
- Full suite: `211 passed in 73.87s`.
