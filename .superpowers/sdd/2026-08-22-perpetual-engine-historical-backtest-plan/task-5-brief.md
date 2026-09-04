# Task 5 brief — Rates, CPI, defensive returns, and allocation

## Scope and files

Implement only Task 5 under TDD.

- Create `perpetual_engine/allocation.py` and `tests/test_allocation.py`.
- Modify `perpetual_engine/data_sources.py` and `config/data_sources_v1.json` only for Task 5 parsers/source metadata.
- Create `task-5-report.md` in this ledger directory.

## Interfaces

- Immutable `SignalSnapshot` and `AllocationState` ledgers.
- `compute_signals(...)` consumes only PIT-admissible ERP, rate and market rows for the supplied `decision_at`.
- `next_allocation(prior, signals, core_weight_pretrade=...)` applies one total precedence and returns deterministic target/flags/trade decision.
- Small source-specific helpers for availability, CPI splice and defensive monthly return may be public where testing requires it.

## Binding data/timing

- Exact FRED IDs: `DGS10`, `DFII10`, `DFF`, `USD1MTD156N`, `SOFR`, `IR3TIB01ITM156N`, `ITACPALTT01IXNBM`; exact Eurostat HICP endpoint from spec §9.
- DGS10/DFII10 without archived release timestamp: next US federal business day at `23:59:59 UTC`; prior-month mean includes only rows available by decision. DFII10 absent pre-2003, no synthetic backfill.
- IR3TIB month M without official timestamp: final calendar day M+1 at `23:59:59 UTC`; can first credit defensive return M+2.
- Defensive return for allocation month t uses latest admissible annual decimal rate: `(1+rate_decimal)^(days_in_month/365)-1`; reject <=-100%, missing or stale.
- CPI: OECD `ITACPALTT01IXNBM` through 2023-11; HICP from 2023-12; mandatory November 2023 link `OECD_Nov/HICP_Nov`, label `ITALY_CPI_PROXY`, continuity exact, no stale FRED as live continuation.
- DFF/LIBOR/SOFR funding parsing itself belongs to Task 6; only freeze config metadata here if shared.

## Signals and percentiles

- Market inputs are prior complete EUR proxy returns only.
- drawdown = prior month-end level/prior running max - 1.
- momentum_1m = prior complete month; momentum_3m = compound last 3; volatility_12m = sample std last 12 * sqrt(12).
- ERP and TIPS percentiles are expanding prior-only midranks: `(count(prior<x)+0.5*count(prior=x))/n`; current excluded; preserve history count. TIPS veto unavailable until >=36 prior admissible observations.
- Every selected row must satisfy `available_at <= decision_at`; month-t outcome cannot select month-t allocation.

## Allocation precedence

1. raw ERP beta: `<3%=0.60`, `[3%,4%)=0.95`, `>=4%=1.00`.
2. Crisis iff DD<=-5%, momentum3<0, vol12>24%: state CRISIS, proposal 0.80, overrides recovery/band.
3. Else if prior state CRISIS/RECOVERY and all recovery conditions (ERP>=4%, 1m>0, 3m>1%, vol<32%, below prior high): state RECOVERY, proposal `min(prior_accepted_final_beta+0.10,1.20)`.
4. Else NORMAL/raw.
5. TIPS cap = minimum applicable: percentile>0.90 =>1.00; percentile>0.95 and ERP percentile<0.60 =>0.90; unavailable => no cap. Cap reduction mandatory.
6. No-trade only NORMAL and only if TIPS did not reduce; HOLD iff `abs(proposed_normal_beta-prior_accepted_final_beta)<0.15`; equality executes. Inception has no band and accepts raw before TIPS.
- Crisis exits immediately when false; recovery persists only while all conditions true. Missing 12-return history makes Crisis/Recovery unavailable, not raw beta.

## Weights and trade trigger

- Inception core 0.60. Tactical rule never buys/sells core thereafter.
- Trade only inception or accepted final beta changed. HOLD means zero overlay/defensive trades and no drift correction.
- On trade: `overlay=max((final_beta-core_weight_pretrade)/2,0)`; `defensive=1-core_weight_pretrade-overlay`.
- If final beta below core weight after overlay zero: actual beta=core weight and flag `CORE_FLOOR_BINDING`.
- If defensive would be negative, reduce overlay to make weights sum one and flag `NO_BORROWING_CAP`.
- Weights sum to one within `1e-12`; final requested beta <=1.20.

## Required RED tests

1. Raw-beta exact 3%/4% boundaries.
2. Crisis/recovery entry, exit, ramp and precedence conflict.
3. TIPS 90/95 conflict uses 0.90 and unavailable before 36.
4. No-trade `<0.15` holds, equality executes; no band at inception/Crisis/Recovery/TIPS reduction.
5. Expanding prior-only midrank with ties and history counts.
6. PIT source timing: Treasury month-end next-business-day exclusion; IR3TIB M-to-M+2.
7. CPI November link, December switch, continuity and missing-link fail.
8. Prior-only market signals and missing warm-up behavior.
9. Core floor, no borrowing, weights invariant, and HOLD zero tactical trade.

Run focused and full suites; record RED/GREEN and all changed files. No network in tests, no optimization, no Task 6 leverage implementation.
