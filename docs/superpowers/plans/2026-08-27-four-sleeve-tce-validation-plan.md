# Four-Sleeve + TCE Validation v1.0 Implementation Plan

**Goal:** Produce reproducible, point-in-time research evidence for the four-sleeve portfolio and the TCE selector without changing the approved E+G or Phase 2 engines.

**Architecture:** Reuse the existing observation, manifest, staleness, cost, metric, and deterministic-output contracts. Add only standalone four-sleeve and TCE modules, configurations, parsers, tests, and reports.

## Task 1: Four-sleeve calculation core

**Files:** `perpetual_engine/four_sleeve.py`, `tests/test_four_sleeve.py`, `config/four_sleeve_v1.json`

1. Write failing tests for fixed candidates, exact weight sums, monthly calendar completeness, explicit end cap, staleness, costs, and error propagation.
2. Implement the smallest fixed-weight backtest and metrics calculator.
3. Verify the focused tests.

## Task 2: Academic proxy parsers

**Files:** `perpetual_engine/market_proxy.py`, `perpetual_engine/data_sources.py`, `tests/test_market_proxy.py`, `tests/test_refresh_parsers.py`, `config/data_sources_v1.json`

1. Add fixtures/tests for exact monthly value-weighted `BIG HiPRIOR`, `BIG Robust`, and AQR TSMOM inputs.
2. Reject wrong tables, columns, units, sentinels, duplicate months, missing internal months, and silent truncation.
3. Add source definitions and deterministic normalized observations.

## Task 3: Frozen data build and 20-year report

**Files:** reuse refresh/manifest pipeline; outputs under `outputs/four_sleeve_v1/`

1. Refresh official/academic sources.
2. Enforce the frozen 2006-06-30 through 2026-05-31 window and 120-day source-aware staleness.
3. Run the four declared portfolios and World benchmark.
4. Inspect calendar, returns, drawdowns, costs, and endpoint concentration.

## Task 4: Deterministic TCE core

**Files:** `perpetual_engine/tce.py`, `tests/test_tce.py`, `config/tce_v1.json`

1. Write failing tests for formula, vetoes, eligibility, missing-data failure, double confirmation, rotation, cash, and deterministic hashes.
2. Implement score and monthly state transitions only.
3. Verify focused tests.

## Task 5: Intramonth events and live ledger

**Files:** `perpetual_engine/tce.py`, `tests/test_tce.py`, `data/tce_live/README.md`

1. Test event whitelist, point-in-time availability, one-session execution lag, five-point hurdle, incumbent floor, and 30-day cooldown.
2. Add the minimal append-only event record and decision function.
3. Start a versioned prospective shadow ledger; historical timestamps remain explicitly reconstructed.

## Task 6: TCE research reports

**Files:** `perpetual_engine/tce_report.py`, `tests/test_tce_report.py`, outputs under `outputs/tce_v1/`

1. Build fixed-universe and thematic result tables.
2. Compare TCE with equal-weight, static, World, and 10% portfolio counterfactuals.
3. Add chronological split, episode sensitivity, delayed/randomized placebo checks, costs, and turnover.

## Task 7: Independent review and full verification

1. Have a non-authoring agent inspect PIT joins, score rules, data coverage, costs, and labels.
2. Run focused tests, then the complete test suite.
3. Inspect normalized data and manifests before reporting any result as validated.
