# Task 7 implementation report — Deterministic portfolio backtest and comparators

## Scope delivered

- Added immutable `BacktestRow`, `BacktestResult`, `BacktestMetadata`, `SummaryMetrics`, `CostDecomposition`, and `BacktestConfig` records.
- Added `run_backtest()`, `summary_metrics()`, and byte-stable `normalized_backtest_json()`.
- Implemented exactly the five frozen strategies, each with independent sleeve notionals.
- Implemented Decimal accounting in the approved order: pretrade NAV, gross targets, per-sleeve order decision/costs, post-cost NAV, then month returns.
- Preserved tactical drift, trigger-only overlay/defensive trading, immutable tactical core after inception, fixed January-only rebalancing, and benchmark buy-and-hold.
- Applied World 0.20% once and required Task 6's exact monthly 0.60% leveraged fee layer without reapplying it.
- Added separate commission, spread, funding, World fee, leveraged fee, and residual-drag ledger/summary fields.
- Added PIT/calendar/return/wipeout/no-borrowing checks and deterministic normalized JSON.
- Reconciles every Task 6 leveraged return layer with a fixed Decimal tolerance and records allocation flags and multiplicative EUR wealth effects.
- Isolates public accounting, metrics, and serialization from the process-global Decimal context at precision 50.
- No network, tax approximation, optimization, Monte Carlo, GDI, CLI, or report-generation work was added.

## RED evidence

Initial complete Task 7 tests were written before `perpetual_engine/backtest.py`.

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
ERROR tests/test_backtest.py
ModuleNotFoundError: No module named 'perpetual_engine.backtest'
```

After the first GREEN pass, a contract audit added two missing guards before their implementation:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
2 failed, 13 passed
- exact Task 6 monthly 0.60% fee layer was not rejected when absent/wrong
- a history beginning after January 1979 was not rejected
```

A final mutation check also proved RED for an inconsistent Task 6 record carrying `wiped_out=True` with a return other than -100%; the guard is now bidirectional.

Two intermediate failures came from test sentinels rather than production behavior: an annual-rebalance fixture skipped required contiguous months, and hand-entered metric literals were incorrect. The fixture was made contiguous and the literals were independently recalculated before continuing.

## GREEN evidence

Focused checkpoint:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
15 passed in 0.67s
```

Full-suite checkpoint:

```text
.venv\Scripts\python.exe -m pytest -q
123 passed in 2.39s
```

## Fix round 1

The independent review identified six Important issues. Each was reproduced under TDD before changing production code:

1. Non-wipeout `LeveragedReturn` records could carry mutually inconsistent underlying/funding/residual/FX/fee totals.
2. Residual drag and ETF fee EUR costs used the starting notional instead of their approved multiplicative bases.
3. `AllocationState.flags` were discarded by the backtest row.
4. Decimal calculations inherited the caller's process-global precision.
5. `FIXED_60_20_20.actual_beta` was hardcoded to one during drift months.
6. Metadata omitted the actual capital/cost inputs and scenario identifiers.

Fix-round RED checkpoint:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
7 failed, 13 passed
```

The seven causal failures covered fixed beta, immutable allocation flags, non-wipeout reconciliation, wipeout coherence, Decimal-context isolation, metadata identity, and multiplicative cost bases. A final mutation test separately failed when the first claimed wipeout had `underlying + funding > -1`.

Fix-round GREEN checkpoints:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
20 passed in 0.67s

.venv\Scripts\python.exe -m pytest -q
128 passed in 2.27s
```

Fix-round implementation details:

- Non-wipeout validation uses `gross = underlying + funding`, reconciles `return_usd` after residual drag, then reconciles `total_return` after FX and ETF fee within relative Decimal tolerance `1e-24`.
- Wipeout requires `gross = underlying + funding <= -1`, `total_return == return_usd == -1`, zero residual/FX/ETF fee, and the wipeout flag. A record with `gross > -1` cannot be marked wiped out.
- Funding cost is `-V*funding`; residual cost is `-V*(1+gross)*residual_drag`; ETF fee is `-V*(1+before_fee)*etf_fee`. Deduction fields remain positive for the approved negative cost rates.
- Underlying, FX, wipeout, and total leveraged wealth effects are recorded and reconciled to ending overlay wealth.
- `allocation_flags` is a frozen tuple and preserves `CORE_FLOOR_BINDING`, `NO_BORROWING_CAP`, and other allocation state flags.
- `actual_beta` for the fixed comparator is the post-target/pre-cost diagnostic `target_core_weight + 2*target_overlay_weight` every month.
- `BacktestMetadata` records config ID, initial capital, commission, spread, spread scenario, and both fee-scenario identifiers.

## Fix round 2

The second independent review identified three Important issues. All three were reproduced before production changes:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
3 failed, 20 passed
```

The RED cases proved that downstream positive FX could incorrectly rescue gross leveraged return at or below -100%, wipeout decomposition erased underlying/funding effects, and global Decimal rounding changed rows and JSON.

Fix-round GREEN checkpoints:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest.py -q
23 passed in 0.88s

.venv\Scripts\python.exe -m pytest -q
131 passed in 3.04s
```

Fix-round implementation details:

- Every monthly record with `gross <= -1` must be wiped out. Downstream residual, FX, or ETF fee cannot rescue a wiped gross return; chronological persistence and explicit reset semantics are documented in Fix round 3.
- Wipeout preserves `leveraged_underlying_effect_eur = V*underlying` and `funding_eur = -V*funding`; residual, FX, and fee effects are zero.
- `leveraged_wipeout_effect_eur = -V*(1+gross)` is a positive clamp correction and is added in wealth reconciliation. The numeric sentinel `V=200`, underlying `-1`, funding `-0.2` reconciles `200 - 200 - 40 + 40 = 0`, while summary funding remains EUR 40.
- Every fixed local Decimal context uses precision 50 and `ROUND_HALF_EVEN`, including fee constants, row accounting through `run_backtest`, `summary_metrics`, public cost properties, and normalized serialization.

## Fix round 3

The third independent review found that wipeout validation treated records in isolation. TDD reproduced both missing behaviors before production changes:

```text
.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py::LeveragedProxyTests::test_monthly_reset_is_audited_even_when_the_new_month_wipes_out_again tests/test_backtest.py::BacktestMetricsAndValidationTests::test_real_task_six_wipeout_persists_until_an_explicit_capital_reset -q
2 failed
- Task 6 reset output omitted CAPITAL_RESET
- Task 7 rejected a valid persistent wipeout whose new gross return was above -100%
```

Fix-round GREEN checkpoints:

```text
.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py::LeveragedProxyTests::test_monthly_reset_is_audited_even_when_the_new_month_wipes_out_again tests/test_backtest.py::BacktestMetricsAndValidationTests::test_real_task_six_wipeout_persists_until_an_explicit_capital_reset -q
2 passed in 0.84s

.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py tests/test_backtest.py -q
37 passed in 0.96s

.venv\Scripts\python.exe -m pytest -q
133 passed in 2.98s
```

Fix-round implementation details:

- Leveraged inputs are validated in chronological order. Gross return at or below -100% always wipes out; a first/non-wiped state cannot claim wipeout when gross is above -100%.
- Once wiped out, later records must remain wiped out even when their current gross return is above -100%, until an explicit immutable `CAPITAL_RESET` flag appears.
- On a reset record the gross rule is applied afresh: gross at or below -100% remains wiped out; gross above -100% may recover. Removing the flag from the same recovery input fails closed, so the primary backtest never invents a reset.
- Minimal Task 6 compatibility change: `leveraged_monthly_return(reset=True)` now records `CAPITAL_RESET`, together with `LEVERAGED_SLEEVE_WIPEOUT` when the reset month immediately wipes out again.
- The end-to-end sentinel uses three actual Task 6 outputs: initial gross wipeout, persistent wiped state despite positive subsequent gross, then explicit reset and a newly investable overlay.

## Fix round 4

The fourth independent review found that chronological validation prevented invalid recovery records but portfolio targeting could still refinance a wiped leveraged sleeve, and `BacktestRow` did not retain the producer flags. Tests were added before production changes and failed on both observable omissions:

```text
.venv\Scripts\python.exe -m pytest tests/test_backtest.py::BacktestAccountingTests::test_wipeout_lock_blocks_fixed_and_tactical_overlay_without_refinancing_costs tests/test_backtest.py::BacktestMetricsAndValidationTests::test_real_task_six_wipeout_persists_until_an_explicit_capital_reset tests/test_backtest.py::BacktestMetricsAndValidationTests::test_leveraged_flags_make_failed_reset_distinct_from_plain_persistence -q
3 failed
- a CRISIS-to-RECOVERY beta change refinanced the wiped overlay
- LEVERAGED_WIPEOUT_LOCK was absent
- BacktestRow had no leveraged_flags audit field
```

Fix-round GREEN checkpoints:

```text
.venv\Scripts\python.exe -m pytest tests/test_backtest.py::BacktestAccountingTests::test_wipeout_lock_blocks_fixed_and_tactical_overlay_without_refinancing_costs tests/test_backtest.py::BacktestMetricsAndValidationTests::test_real_task_six_wipeout_persists_until_an_explicit_capital_reset tests/test_backtest.py::BacktestMetricsAndValidationTests::test_leveraged_flags_make_failed_reset_distinct_from_plain_persistence -q
3 passed in 1.33s

.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py tests/test_backtest.py -q
39 passed in 1.53s

.venv\Scripts\python.exe -m pytest -q
135 passed in 4.81s
```

Fix-round implementation details:

- A chronological `leveraged_wipeout_locked` decision state begins in the month after a wipeout. The first wipeout month retains its PIT target because its outcome was not yet known at the decision instant.
- While locked, every overlay strategy targets zero overlay and assigns the residual to defensive. Tactical core remains untraded; the fixed comparator may still execute its normal January core rebalance, with zero overlay and the remaining 40% defensive.
- If the prior wipeout already left overlay at zero, the locked month creates no overlay trade, commission, or spread. `LEVERAGED_WIPEOUT_LOCK` is recorded in `allocation_flags`.
- `accepted_final_beta` remains the signal/fixed request. `actual_beta` is recalculated from the enforced post-target/pre-cost weights as `core + 2*overlay`.
- `CAPITAL_RESET` removes the lock for its own decision month. A successful reset can buy overlay; a reset month that wipes out again restarts the lock from the following month.
- Frozen `BacktestRow.leveraged_flags` copies Task 6 flags exactly. Normalized JSON serializes the tuple deterministically, making simple persistence and failed reset byte-distinct and auditable.

## Tests added

1. Exact pretrade → target → own-cost → return numeric sentinel.
2. January 1979 initial order counts and commissions for benchmark/fixed/tactical strategies.
3. Per-sleeve costs and `COST_EXCEEDS_SLEEVE_VALUE` failure.
4. Tactical HOLD drift preservation and trigger-only overlay/defensive trades.
5. Fixed comparator January-only rebalancing across a contiguous 13-month fixture.
6. Exactly five strategies and Damodaran/Crisis/TIPS feature isolation.
7. World fee exactly once and leveraged fee not reapplied.
8. Exact Task 6 leveraged fee-layer validation.
9. CAGR, sample volatility, Sharpe, maximum drawdown, turnover, and unavailable Sharpe sentinels.
10. Separate funding, World fee, leveraged fee, residual drag, commission, and spread totals.
11. Calendar alignment, bounds, chronologically consistent/persistent wipeout with explicit capital reset and post-wipeout target lock, weights/no-borrowing, and exact decision timing.
12. Exact January 1979 start.
13. Month-t outcomes alter endings but not their own targets.
14. Byte-identical normalized JSON and frozen config values.
15. Public record and exact leveraged-flag tuple immutability.
16. Fixed/tactical no-refinancing lock, annual fixed-core rebalance under lock, reset success/failure, and byte-distinct reset audit.

## Files changed

- `perpetual_engine/backtest.py` — new deterministic accounting, strategies, metrics, validation, and serialization.
- `perpetual_engine/leveraged_proxy.py` — Task 6 compatibility audit flag for explicit capital resets.
- `tests/test_backtest.py` — new Task 7 RED/GREEN contract suite.
- `tests/test_leveraged_proxy.py` — Task 6 audit test for reset and immediate re-wipeout flags.
- `config/backtest_v1.json` — frozen primary costs, sensitivities, fee layers, semantics, and five strategy names.
- `.superpowers/sdd/2026-08-22-perpetual-engine-historical-backtest-plan/task-7-report.md` — this evidence report.

No compatibility changes were needed in `allocation.py` or `market_proxy.py`; Fix round 3 added the explicitly authorized Task 6 audit flag in `leveraged_proxy.py`.

## Explicit assumptions

- The defensive monthly return series is the configured risk-free series used by `run_backtest()` for Sharpe; the public `summary_metrics()` accepts an explicit same-calendar risk-free mapping.
- Mapping inputs provide uniqueness by construction; the engine additionally sorts and validates month-end keys, exact month-set equality, and continuity.
- The primary backtest never infers or creates a capital reset. Once Task 6 reports wipeout, later inputs must remain wiped out until the producer explicitly supplies immutable `CAPITAL_RESET`; that record then reapplies the gross wipeout rule.
- A wipeout is an allocation constraint only from the following month, preserving PIT timing. `CAPITAL_RESET` authorizes targets in its own month; if its return wipes out again, the constraint resumes in the next month.
- Leveraged EUR cost fields use the approved sequential multiplicative bases; original Task 6 component rates and a complete wealth-effect reconciliation remain in every row.
- `actual_beta` is defined consistently as a post-target/pre-cost diagnostic; post-cost diagnostic weights remain available separately in the monetary ledger.
- Decimal calculations use local precision 50 with explicit `ROUND_HALF_EVEN`. Reconciliation comparisons use relative tolerance `1e-24` to accept Task 6 values rounded by their producing context without accepting material mismatches.
- A gross trade below EUR 0.01 retains full-precision target accounting but does not create an order, commission, or spread charge, matching the approved “order decision only” threshold.
