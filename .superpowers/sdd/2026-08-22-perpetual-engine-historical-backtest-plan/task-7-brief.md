# Task 7 brief — Deterministic portfolio backtest and comparators

## Scope/files

Implement only Task 7 under TDD.

- Create `perpetual_engine/backtest.py`, `tests/test_backtest.py`, `config/backtest_v1.json`.
- Create `task-7-report.md` in this ledger directory.
- Reuse allocation/leveraged records; make only minimal compatibility fixes outside these files if a proven interface blocker exists and document it.

## Public interfaces

- Immutable `BacktestRow`, `BacktestResult`, summary/decomposition records.
- `run_backtest(...)` consumes complete monthly signals, raw World EUR returns, defensive returns, leveraged investable returns and their funding/fee/residual ledgers.
- `summary_metrics(...)` computes metrics from one uninterrupted monthly result.
- Normalized JSON serialization/helper must be byte-stable for the same inputs.

## Frozen strategies — exactly five

1. `WORLD_BENCHMARK`: 100% unleveraged investable World at inception, then buy-and-hold; one initial order only.
2. `FIXED_60_20_20`: 60% unleveraged core, 20% leveraged overlay, 20% defensive; initial deployment and rebalance every January only; otherwise drift.
3. `DAMODARAN_ONLY`: raw ERP beta engine/no Crisis/Recovery/no TIPS.
4. `DAMODARAN_CRISIS_RECOVERY`: ERP plus Crisis/Recovery, no TIPS.
5. `PHASE2_COMPLETE`: full state machine including TIPS veto.

No alternative, optimization, Monte Carlo or GDI strategy in this task. Primary run is accumulation/GROWTH and zero distributions; pre-tax historical results.

## Return layers

- Input raw World EUR proxy is diagnostic. Core/benchmark apply World fee once: `world_fee_month=(1-0.002)^(1/12)-1`; `R_world_investable=(1+raw)*(1+fee)-1`.
- Leveraged input must be the Task 6 monthly investable result with 0.60% fee exactly once; do not reapply it. Preserve Task 6 funding, ETF fee and residual drag as separate ledger components.
- Every sleeve return must be finite and >-1 except an explicitly wiped-out leveraged sleeve return may equal -1. No sleeve recovers from wipeout without explicit Task 6 capital reset/contribution state.

## Monthly accounting order

For each independent strategy/month:

1. `pretrade_NAV` is prior month ending sleeve values (initially EUR 800000 cash/notionals zero).
2. Determine gross targets from pretrade NAV and the permitted strategy trigger; do not re-solve after costs.
3. `trade_i=target_i-pretrade_i`. Absolute trade < EUR 0.01 is rounded to zero for order decision only.
4. Each nonzero sleeve order pays EUR 19 plus `abs(trade_i)*spread_bps/10000` (primary 10 bps; sensitivity config 5/20). Costs remain on that sleeve.
5. Post-cost/pre-return sleeve=`target_i-own_commission-own_spread`; negative => `COST_EXCEEDS_SLEEVE_VALUE` fail.
6. Recompute post-cost NAV/diagnostic weights, then apply month returns.

- January 1979 inception pays each nonzero sleeve order. Benchmark pays one; fixed pays three; tactical strategies pay their nonzero inception sleeves.
- Core units of tactical strategies are never bought/sold after inception. Tactical trades only when accepted final beta changes; HOLD = zero overlay/defensive order and preserves drift.
- Fixed comparator rebalance only every January and pays only nonzero sleeve orders.
- No borrowing; sleeve values/target weights nonnegative and weights sum to one within 1e-12 before own-sleeve costs.

## Timing

- Month t allocation uses a `SignalSnapshot.decision_at` exactly at month t-1 final instant. Validate this relation.
- Month-t returns are outcomes applied only after targets/costs and cannot affect their own decision.
- Input calendar must be contiguous, unique month-ends with the same months across required signals/returns; no fill.

## Metrics/decomposition

- CAGR `prod(1+r)^(12/n)-1`.
- Annualized volatility = sample std monthly (`ddof=1`)*sqrt(12).
- Sharpe = mean monthly `(portfolio_return-risk_free_monthly)` / sample std of monthly excess (`ddof=1`) * sqrt(12); insufficient/zero variance is explicit unavailable, never infinity.
- Max drawdown from wealth path including initial value.
- Turnover by sleeve/month = `abs(executed_trade)/pretrade_NAV`; report aggregate.
- Sum commissions, spread, funding, World fee, leveraged fee and residual drag separately; never infer one from another or combine silently.

## Required RED tests

1. Exact pretrade→target→own-cost→return sequence with numeric sentinel.
2. January 1979 deployment commissions/order counts for benchmark/fixed/tactical.
3. Costs remain on traded sleeve and negative post-cost fails.
4. Tactical HOLD: drift preserved, zero trades/costs; change trigger trades overlay/defensive deltas only, never core.
5. Fixed annual January rebalance and no other rebalance.
6. Five strategies only and feature isolation (Damodaran-only vs Crisis vs full TIPS) on conflict fixtures.
7. World 0.20% exactly once and leveraged 0.60% not reapplied.
8. Metrics numeric sentinels/decomposition/turnover.
9. Contiguous calendar, return bounds, weight/no-borrowing and decision timing.
10. Same inputs produce byte-identical normalized rows/metadata.

Use Decimal for monetary/cost accounting. No network, no tax approximation, no Task 8 CLI/report generation.
