# Task 6 brief — Leveraged World proxy and fee layers

## Scope/files

Implement only Task 6 under TDD.

- Create `perpetual_engine/leveraged_proxy.py` and `tests/test_leveraged_proxy.py`.
- Modify Task 5 source config only if an exact funding field is missing; do not implement backtest accounting.
- Create `task-6-report.md` in this ledger directory.

## Interfaces and ledgers

- `leveraged_daily_return(...)`
- `leveraged_monthly_return(...)`
- `apply_fx_once(...)`
- `validate_leveraged_proxy(...)` with distinct modes/results for licensed daily benchmark, official summaries, and LWLD NAV.
- Immutable result records must expose underlying, funding, residual-drag, FX and ETF-fee effects separately; never conflate or apply twice.
- Monthly sleeve wipeout state must persist: after gross <=-1, output is -100% and remains wiped out until an explicit new capital contribution/reset flag.

## Funding contract

- `DFF` through 1985-12-31, explicitly labelled pre-LIBOR proxy.
- `USD1MTD156N` from 1986-01-01 through 2021-08-31.
- `SOFR` from 2021-09-01.
- Inputs are annual percentages. Accrue actual calendar-day intervals/360. Friday-to-Monday is 3 days.
- Carry latest prior business-day rate at most 7 calendar days; never bridge the LIBOR/SOFR boundary with the wrong source. Missing/wrong-series/nonfinite values fail closed.

## Long monthly approximation

- `funding_t=sum((rate_d/100)*interval_days/360)` over the calendar month.
- `gross=2*R_U-funding_t`.
- Missing-daily-reset residual `d`: primary 0.011, sensitivities only 0.006 and 0.015; `drag_month=(1-d)^(1/12)-1`; multiply after gross.
- If gross <= -1: set sleeve return exactly -1, flag `LEVERAGED_SLEEVE_WIPEOUT`, no residual/fee resurrection.
- FX once after USD result.
- Benchmark excludes ETF fee. Optional investable layer applies 0.60% once as `(1-0.006)^(1/12)-1` after proxy; report TER separately.

## Daily audit

- Starts July 1990. Per interval: `r_L_USD=2*r_U_USD-(rate_pct/100)*calendar_days/360`, compounded daily.
- Daily reset already embeds volatility drag: residual d MUST be absent.
- FX once after leverage.
- Investable fee interval `(1-0.006)^(calendar_days/365)-1`, multiplicative after leverage and FX; only fee-adjusted series is compared with LWLD NAV. Pre-fee benchmark only is compared with official leveraged index.

## Validation

- Licensed daily benchmark from Feb 2014: uninterrupted common daily sample; correlation >=0.995; OLS-with-intercept beta [0.98,1.02]; sample TE `ddof=1*sqrt(252)` <=0.02. PASS status must explicitly mean full daily; missing/insufficient data must not PASS.
- Official summaries only: >=5 common annual returns, each gap <=0.03; 3/5/10y annualized gaps <=0.02 wherever published. Passing status exactly `PASS_PARTIAL_OFFICIAL_SUMMARY`, never full.
- LWLD NAV after 2025 inception: fee-adjusted monthly EUR proxy vs official base-currency USD NAV converted with same DEXUSEU; missing NAV -> `FAIL_MISSING_OFFICIAL_LWLD_NAV`; uninterrupted >=12; corr>=0.95; beta [0.85,1.15]; TE<=0.08; CAGR gap<=0.05; threshold failure `FAIL_LWLD_VALIDATION`.
- Keep benchmark validation and product validation results independent.

## Required RED tests

1. Flat underlying with positive funding loses money.
2. Two-day +10%/-9.09090909% proves daily-reset path dependence.
3. Friday-to-Monday 3-day funding; carry 7 passes, 8 fails.
4. Exact DFF/LIBOR/SOFR boundaries and no LIBOR-to-SOFR bridge.
5. FX applied once: +10% asset with FX factor 0.90 gives -1%.
6. Monthly residual only once and separate from 0.60% TER; daily has no 1.1% subtraction.
7. Gross wipeout clamps -1 and persists until explicit contribution/reset.
8. Daily fee uses actual calendar days and is included exactly once in LWLD validation series, absent from benchmark series.
9. Full daily, official-summary and LWLD validation statuses, sample sizes and exact threshold boundaries.

Use Decimal where needed for deterministic ledger values. No network in tests, no exact-MSCI/LWLD-history claim, no Task 7 portfolio costs.
