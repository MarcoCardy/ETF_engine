# Perpetual Engine Historical Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved point-in-time historical market engine, Damodaran allocation state machine, developed-World and leveraged proxies, and deterministic baseline strategy comparison required before the GDI shadow study.

**Architecture:** Keep downloads in one network-only adapter and freeze every raw artifact before calculation. Normalize all observations into one point-in-time row contract, then run pure allocation and portfolio-accounting functions offline. Reuse the existing JSON/hash conventions and extend the current CLI without changing Rule E or Funding Protocol G beyond the approved gross-fiscal migration.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `datetime`, `decimal`, `csv`, `json`, `hashlib`, `urllib`, `zipfile`, `zoneinfo`, `unittest`), existing `openpyxl==3.1.5`, and existing `numpy` only where daily vector arithmetic materially shortens code. No database, optimizer, provider framework, or web service.

**Spec:** `docs/superpowers/specs/2026-08-21-perpetual-engine-historical-backtest-design.md`

## Global Constraints

- Monthly study begins January 1979; daily leveraged audit begins only where daily inputs exist.
- Every selected input satisfies `available_at <= decision_at`; joins by observation date alone are forbidden.
- Damodaran uses official Stern/NYU files and exact `ERP (T12m)` monthly field from September 2008.
- The public market series is labelled `PUBLIC_DEVELOPED_WORLD_TR_PROXY_USD`, never MSCI World.
- USD returns convert to EUR once with `EUR_per_USD`; daily FX uses last prior fixing with at most seven calendar days of carry.
- Leveraged returns reset daily; funding, ETF fee, and residual drag are separate layers with no double counting.
- Rule E targets EUR 1,800 gross and 3% gross; tax lowers delivered net and never causes gross-up.
- Calculation and tests are offline; `data refresh` is the only networked command.
- Same frozen inputs, config, and code produce byte-identical normalized JSON.
- The workspace is not a Git repository. Replace commit steps with passing-test checkpoints; do not initialize Git without user authorization.

---

### Task 1: Correct Rule E to gross-fiscal targets

**Files:**
- Modify: `perpetual_engine/models.py`
- Modify: `perpetual_engine/io.py`
- Modify: `perpetual_engine/policy.py`
- Modify: `perpetual_engine/funding.py`
- Modify: `config/policy_v1.json`
- Modify: `tests/helpers.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_policy.py`
- Modify: `tests/test_funding.py`

**Interfaces:**
- Produces: `PolicyConfig.protected_monthly_gross`, `PolicyDecision.target_gross_real`, `target_gross_real()`, `FundingResult.delivered_net_real`.
- Preserves: existing `evaluate_policy()` and `fund_withdrawal()` entry points.

- [ ] **Step 1: Rename the config and decision fields in failing tests**

```python
def test_protected_target_is_gross_fiscal(self):
    result = target_gross_real(money("640000"), money("650000"), Lifecycle.DISTRIBUTION, self.config)
    self.assertEqual(result.target_gross_real, money("1800"))

def test_tax_reduces_net_without_gross_up(self):
    result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="5000"))
    self.assertEqual(result.total_outflow_real, money("1800"))
    self.assertLess(result.delivered_net_real, money("1800"))
```

- [ ] **Step 2: Run focused tests and verify the old net names fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_policy tests.test_funding -v`

Expected: failures naming `protected_monthly_gross` or `target_gross_real` as missing.

- [ ] **Step 3: Apply the minimal gross-target migration**

Rename JSON key `protected_monthly_net` to `protected_monthly_gross`. In funding, stop when `delivered_net + tax + commission + spread` reaches the gross target or floor cap; never solve for a sale that restores net payout to the target.

```python
gross_budget = min(decision.target_gross_real, decision.maximum_total_outflow_real)
remaining_budget = gross_budget - total_outflow
```

- [ ] **Step 4: Update deterministic JSON/CSV field names and compatibility rejection**

Reject the old JSON key with a clear `KeyError`; do not support two policy meanings simultaneously. CSV columns become `target_gross_real`, `delivered_net_real`, and `total_outflow_real`.

- [ ] **Step 5: Run the full checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all current and gross-fiscal tests pass.

---

### Task 2: Point-in-time row and frozen manifest contracts

**Files:**
- Create: `perpetual_engine/point_in_time.py`
- Create: `perpetual_engine/data_sources.py`
- Create: `tests/test_point_in_time.py`
- Create: `tests/test_data_sources.py`
- Modify: `perpetual_engine/io.py`

**Interfaces:**
- Produces: `ObservationRow`, `SourceArtifact`, `asof_select()`, `freeze_bytes()`, `load_observation_csv()`.
- Consumes: existing `sha256_file()` and normalized JSON conventions.

- [ ] **Step 1: Write failing row-validation and as-of tests**

```python
from datetime import date, datetime, timezone
from decimal import Decimal
from perpetual_engine.point_in_time import ObservationRow, asof_select

def test_asof_never_selects_future_release(self):
    rows = (
        ObservationRow(
            series_id="ERP",
            observation_date=date(2007, 12, 31),
            period_end=date(2007, 12, 31),
            available_at=datetime(2008, 2, 1, 23, 59, 59, tzinfo=timezone.utc),
            value=Decimal("0.05"),
            unit="ratio",
            source_url="https://pages.stern.nyu.edu/source.xls",
            retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
            source_hash="a" * 64,
        ),
    )
    self.assertIsNone(asof_select(rows, datetime(2008, 2, 1, 12, tzinfo=timezone.utc)))
    self.assertEqual(asof_select(rows, datetime(2008, 3, 1, tzinfo=timezone.utc)), rows[0])
```

- [ ] **Step 2: Run and confirm import failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_point_in_time -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement immutable normalized rows**

```python
@dataclass(frozen=True)
class ObservationRow:
    series_id: str
    observation_date: date
    period_end: date
    available_at: datetime
    value: Decimal
    unit: str
    source_url: str
    retrieved_at: datetime
    source_hash: str
    vintage_status: str = "CURRENT_VINTAGE_RESEARCH"
```

Validate timezone-aware UTC timestamps, finite values, non-empty provenance, `period_end >= observation_date`, and unique `(series_id, observation_date, source_hash)`.

- [ ] **Step 4: Implement frozen artifacts without a provider abstraction**

`freeze_bytes(content, url, retrieved_at, raw_dir)` writes `raw_dir/<sha256>/<original-name>` and a sorted-key manifest containing URL, retrieval time, content hash, byte count, and parser version. Existing hashes are reused byte-for-byte; changed content creates a new directory.

- [ ] **Step 5: Add rejection tests**

Test duplicate dates, naive timestamps, changed hash without vintage identity, non-finite values, and `available_at > decision_at` selection. Run the full suite.

---

### Task 3: Damodaran ERP parser and release timing

**Files:**
- Modify: `perpetual_engine/data_sources.py`
- Create: `tests/test_damodaran.py`
- Create: `config/data_sources_v1.json`

**Interfaces:**
- Produces: `parse_damodaran_annual(path, artifact) -> tuple[ObservationRow, ...]`, `parse_damodaran_monthly(path, artifact) -> tuple[ObservationRow, ...]`.
- Consumes: `ObservationRow`, `SourceArtifact` from Task 2.

- [ ] **Step 1: Write parser tests with workbooks created in memory**

Create minimal `openpyxl.Workbook` fixtures containing the exact sheets and columns. Assert annual 2007 receives conservative availability on 2008-02-01 and monthly September 2008 selects only `ERP (T12m)`.

```python
self.assertEqual(rows[0].series_id, "DAMODARAN_ERP_T12M")
self.assertEqual(rows[0].observation_date.isoformat(), "2008-09-01")
```

- [ ] **Step 2: Add stale/wrong-source failures before implementation**

Reject non-`pages.stern.nyu.edu` domains, root-level stale monthly URL, wrong sheet, `Sustainable ERP`, null/non-numeric cells, duplicates, and a maximum date older than the config freshness limit.

- [ ] **Step 3: Implement exact annual and monthly selectors**

Freeze URLs:

```text
annual = https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls
monthly = https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx
monthly_sheet = Historical ERP
monthly_field = ERP (T12m)
```

Use actual release timestamps when present; otherwise apply the spec’s annual February-1 and monthly date-only conservative cutoffs.

- [ ] **Step 4: Test the September-2008 switch and no retroactive backfill**

Assert August 2008 uses the annual series, September source begins exactly at 2008-09, and a date-only September release cannot affect the September close.

- [ ] **Step 5: Run parser and full-suite checkpoints**

Run: `.venv\Scripts\python.exe -m unittest tests.test_damodaran -v`

Then run all tests.

---

### Task 4: Developed-World proxy, FX, and validation

**Files:**
- Create: `perpetual_engine/market_proxy.py`
- Create: `tests/test_market_proxy.py`
- Modify: `config/data_sources_v1.json`

**Interfaces:**
- Produces: `build_public_world_monthly()`, `convert_usd_to_eur()`, `validate_world_proxy()`.
- Consumes: frozen French, WDI, FX, and optional official NAV rows.

- [ ] **Step 1: Write failing splice and FX sentinel tests**

```python
def test_fx_direction(self):
    self.assertAlmostEqual(convert_usd_to_eur(0.10, 1.0, 0.9), -0.01, places=12)

def test_splice_switches_exactly_in_july_1990(self):
    result = build_public_world_monthly(inputs)
    self.assertEqual(result[date(1990, 6, 30)].segment, "RECONSTRUCTED_1979_1990")
    self.assertEqual(result[date(1990, 7, 31)].segment, "FF_DEVELOPED")
```

- [ ] **Step 2: Implement exact French archive parsers**

Parse the configured zip member and exact columns from the spec. Divide percent returns by 100 once. For 1979–June 1990, combine US and non-US returns with WDI `CM.MKT.LCAP.CD` USA/World two-year-lag weights. Fail if weight is absent or outside `[0,1]`.

- [ ] **Step 3: Implement FX normalization and calendars**

Normalize `CCUSSP01DEM650N` and `DEXUSEU` from raw `USD_per_EUR` to `EUR_per_USD = 1/raw`. Extend the synthetic DEM segment through January 1977. Daily FX reindexes to the underlying calendar using the latest prior fixing with at most seven calendar days carry.

- [ ] **Step 4: Implement validation metrics exactly**

```python
@dataclass(frozen=True)
class ProxyMetrics:
    n: int
    correlation: float
    beta: float
    tracking_error: float
    cagr_gap: float
    max_drawdown_gap: float
    status: str
```

Use OLS slope with intercept, sample standard deviation `ddof=1`, uninterrupted common sample, and pre-registered thresholds. Missing official NAV returns `VALIDATION_UNAVAILABLE`, never PASS.

- [ ] **Step 5: Add coverage and label tests**

Assert January 1979 onward has no duplicates/gaps, warm-up covers January 1977–December 1978, and output is always labelled `PUBLIC_DEVELOPED_WORLD_TR_PROXY_USD`.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run market-proxy tests, then the complete suite.

---

### Task 5: Rates, CPI, defensive returns, and allocation state machine

**Files:**
- Create: `perpetual_engine/allocation.py`
- Create: `tests/test_allocation.py`
- Modify: `perpetual_engine/data_sources.py`
- Modify: `config/data_sources_v1.json`

**Interfaces:**
- Produces: `SignalSnapshot`, `AllocationState`, `compute_signals()`, `next_allocation()`.
- Consumes: PIT ERP, rates, CPI, defensive-return and World-return rows.

- [ ] **Step 1: Write exact state-boundary tests**

Cover raw beta, crisis compression, recovery ramp, TIPS cap conflicts, and no-trade boundary:

```python
self.assertEqual(apply_no_trade(prior=0.80, proposed=0.949999), 0.80)
self.assertEqual(apply_no_trade(prior=0.80, proposed=0.95), 0.95)
self.assertEqual(min_applicable_tips_cap(percentile=0.96, erp_percentile=0.50), 0.90)
```

- [ ] **Step 2: Implement source-specific PIT fallback timing**

DGS10/DFII10 daily values become available only under their next-release rule; monthly IR3TIB uses the conservative M+1 fallback and first credits M+2. Implement the CPI splice at November 2023 exactly as specified and fail on either missing link observation.

- [ ] **Step 3: Implement expanding prior-only percentiles**

Use the Phase 2 percentile contract, including exact ties; never fit on the full sample. Preserve percentile history count in the signal ledger.

- [ ] **Step 4: Implement one total precedence function**

`next_allocation(prior, signals)` applies inception, Crisis, Recovery, TIPS minimum cap, core floor/no borrowing, and finally the no-trade decision exactly in spec order. `HOLD` carries prior accepted final beta and executes no overlay/defensive trade.

- [ ] **Step 5: Add invariant tests**

Assert weights sum to one within `1e-12`, the core is never sold tactically, conflicting conditions produce the expected state, and every signal input has `available_at <= decision_at`.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run allocation tests, then all tests.

---

### Task 6: Leveraged World proxy and fee layers

**Files:**
- Create: `perpetual_engine/leveraged_proxy.py`
- Create: `tests/test_leveraged_proxy.py`

**Interfaces:**
- Produces: `leveraged_daily_return()`, `leveraged_monthly_return()`, `validate_leveraged_proxy()`.
- Consumes: World USD returns, funding rows, FX rows, and fee config.

- [ ] **Step 1: Write the reset/funding/FX tests**

```python
def test_flat_market_loses_funding(self):
    self.assertLess(leveraged_daily_return(0.0, 0.05, 1), 0.0)

def test_two_day_reset_path(self):
    wealth = (1 + leveraged_daily_return(0.10, 0, 1)) * (1 + leveraged_daily_return(-0.0909090909, 0, 1))
    self.assertNotAlmostEqual(wealth, 1.0)

def test_fx_is_not_doubled(self):
    self.assertAlmostEqual(apply_fx_once(0.10, 0.90), -0.01)
```

- [ ] **Step 2: Implement funding benchmark transitions**

Use DFF, LIBOR, and SOFR periods exactly from the spec; accrue Friday-to-Monday over three calendar days and reject a wrong-source bridge.

- [ ] **Step 3: Implement daily reset and monthly approximation**

```python
r_leveraged_usd = 2 * r_world_usd - funding_rate * calendar_days / 360
fee_day = (1 - Decimal("0.006")) ** (Decimal(calendar_days) / Decimal("365")) - 1
```

Apply ETF fee only to the investable proxy, never the official MSCI benchmark validation. Keep 0.60% TER and residual drag in separate ledger columns.

- [ ] **Step 4: Add wipeout and validation-status tests**

Cap a monthly gross leveraged return at `-1`, never below. Test official-summary, daily-level, and LWLD NAV validation statuses independently.

- [ ] **Step 5: Run focused and full-suite checkpoints**

Run leveraged tests, then all tests.

---

### Task 7: Deterministic portfolio backtest and comparators

**Files:**
- Create: `perpetual_engine/backtest.py`
- Create: `tests/test_backtest.py`
- Create: `config/backtest_v1.json`

**Interfaces:**
- Produces: `BacktestRow`, `BacktestResult`, `run_backtest()`, `summary_metrics()`.
- Consumes: allocations, market/defensive/leveraged returns, and cost config.

- [ ] **Step 1: Write failing timing and cost-ledger tests**

Assert the sequence `pretrade NAV -> gross targets -> own-sleeve costs -> returns`, initial deployment costs, and zero trade/cost on unchanged accepted beta.

- [ ] **Step 2: Implement monthly portfolio accounting**

Each comparator owns independent sleeve notionals. Costs are deducted from the sleeve traded, not another strategy. Preserve drift between permitted rebalances and prohibit borrowing.

```python
post_cost_notional = pretrade_target_notional - commission - abs(trade_notional) * spread_bps / 10_000
ending_notional = post_cost_notional * (1 + sleeve_return)
```

- [ ] **Step 3: Implement the frozen comparator set**

Run the strategies named in §15 only. Apply World 0.20% and leveraged 0.60% expense layers once using `(1-fee) ** (1/12) - 1` in monthly investable runs.

- [ ] **Step 4: Implement metrics and decompositions**

Calculate CAGR, annualized volatility, Sharpe with configured defensive/risk-free series, maximum drawdown, turnover, commissions, spread, funding, ETF fees, and residual drag from ledger rows. Do not add optimization or Monte Carlo.

- [ ] **Step 5: Add byte-stability and invariants**

Assert weights sum to one, returns exceed `-100%`, same inputs yield identical normalized rows, and no month-t outcome affects month-t decision.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run backtest tests, then all tests.

---

### Task 8: Network refresh, offline CLI, outputs, and independent gate

**Files:**
- Modify: `perpetual_engine/cli.py`
- Create: `perpetual_engine/backtest_report.py`
- Create: `tests/test_backtest_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces commands `data refresh` and `backtest` from the approved spec.
- Produces frozen manifests, PIT ledgers, equity curves, metrics, validation, and run metadata.

- [ ] **Step 1: Write CLI isolation tests**

Patch `urllib.request.urlopen` to raise during `backtest`; assert the offline command still succeeds from fixtures. Patch it during `data refresh` and assert the network adapter is the only caller.

- [ ] **Step 2: Implement `data refresh`**

Add:

```text
python -m perpetual_engine data refresh --config config/data_sources_v1.json
```

Download configured artifacts, freeze bytes, parse to normalized CSV, and write raw/derived manifests. A failed source leaves prior frozen data untouched and returns nonzero.

- [ ] **Step 3: Implement offline `backtest`**

Add:

```text
python -m perpetual_engine backtest --config config/backtest_v1.json --output outputs/backtest_v1
```

Write sorted normalized JSON/CSV files for signals, allocations, curves, metrics, costs, proxy validations, and run manifest.

- [ ] **Step 4: Add reproducibility and malformed-input tests**

Run the fixture backtest twice into temporary directories and compare every relative file path and byte. Corrupt one hash and assert failure before calculation.

- [ ] **Step 5: Update operator documentation**

Document refresh vs offline execution, evidence labels, licensed-data limitations, and that this phase is pre-tax except for corrected Rule E tests.

- [ ] **Step 6: Run final verification**

Run:

```powershell
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m unittest discover -s tests -v
rg -n "[T]BD|[T]ODO|implement later|MSCI World history" perpetual_engine tests config README.md
```

Expected: dependency check clean, zero test failures/errors, no placeholders, and no public proxy mislabeled as MSCI.

- [ ] **Step 7: Independent implementation review**

Dispatch an agent who authored none of the implementation. Require it to read the spec, diff-equivalent file list, manifests, and test output; it must issue `PASS` or `FAIL` with file/line references. Any FAIL is fixed and re-reviewed before this plan is complete.
