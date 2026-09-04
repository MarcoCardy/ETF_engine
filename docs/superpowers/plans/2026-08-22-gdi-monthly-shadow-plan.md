# GDI Monthly Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved monthly Global Debasement Index, arbitrary-day nowcast, weekly Gold/SDR Market Pulse, Value/Growth validation, fixed shadow satellite, and auditable report/workbook exports.

**Architecture:** Extend the Phase 2 point-in-time and frozen-source infrastructure instead of creating a second data framework. Institutional-source parsers create normalized observations; pure functions calculate monthly scores, states, validation, and shadow returns offline. Reporting consumes deterministic exported ledgers and never performs calculations or downloads.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `datetime`, `decimal`, `csv`, `json`, `math`, `statistics`, `urllib`, `zipfile`, `zoneinfo`, `unittest`), existing `openpyxl==3.1.5`, and existing `yfinance==1.6.0` only for the labelled intramonth `GC=F` diagnostic. No optimizer, database, scheduler, provider framework, or new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-21-gdi-monthly-shadow-design.md`

**Prerequisite:** Complete `docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md`; this plan consumes `ObservationRow`, PIT selection, FX, baseline returns, costs, and offline backtest interfaces produced there.

## Global Constraints

- Official GDI is monthly, observational, and non-authoritative; only a separately approved future design may make it live.
- Raw history begins exactly 1996-01, percentile history begins exactly 1997-01, and first target is 2000-01.
- Percentiles are expanding current-inclusive midranks with at least 36 valid observations; provisional ranks use the frozen virtual-append formula.
- `GDI = 0.40*G + 0.35*M + 0.25*Rstar`; weights, thresholds, lags, smoothing, horizons, and satellite sizes are never optimized.
- Every official selected input satisfies `available_at <= decision_at`; current-vintage revisions are labelled honestly.
- Public official Gold is World Bank monthly average times IMF in-month average SDR-per-USD; daily `GC=F` is diagnostic only.
- Missing/stale data fail closed. `HOLD_DATA_INCOMPLETE` preserves the last accepted shadow holding and executes no trade.
- Prudent rule is tested first; symmetric high=Value/low=Growth is tested second with frozen rules.
- Style study uses indices/proxies; ETF selection waits until the mathematical study is frozen.
- Report/workbook clearly separate official, provisional, proxy, and incomplete results.
- Calculation and tests are offline; only `data refresh` may access the network.
- Same frozen inputs, config, and code produce byte-identical normalized JSON/CSV.
- The workspace is not a Git repository. Replace commit steps with passing-test checkpoints; do not initialize Git without user authorization.

---

### Task 1: GDI configuration, domain results, and calendar contract

**Files:**
- Create: `config/gdi_v1.json`
- Create: `perpetual_engine/gdi.py`
- Create: `tests/test_gdi_models.py`
- Modify: `perpetual_engine/io.py`

**Interfaces:**
- Consumes: `ObservationRow` and UTC/PIT helpers from the prerequisite plan.
- Produces: `GdiConfig`, `GdiComponents`, `GdiMonth`, `GdiStatus`, `PrudentState`, `SymmetricState`, `load_gdi_config()`.

- [ ] **Step 1: Write failing config and boundary tests**

```python
from datetime import date
from perpetual_engine.gdi import GdiConfig, regime_label

def test_frozen_calendar_and_weights(self):
    cfg = GdiConfig.default()
    self.assertEqual(cfg.raw_start, date(1996, 1, 1))
    self.assertEqual(cfg.percentile_start, date(1997, 1, 1))
    self.assertEqual((cfg.gold_weight, cfg.money_weight, cfg.repression_weight), (0.40, 0.35, 0.25))

def test_regime_boundaries(self):
    self.assertEqual(regime_label(40.0), "NORMAL")
    self.assertEqual(regime_label(55.0), "SIGNIFICANT_DEBASEMENT")
    self.assertEqual(regime_label(85.0), "EXTREME_MONETARY_REGIME")
```

- [ ] **Step 2: Run and confirm missing-module failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_gdi_models -v`

Expected: import failure for `perpetual_engine.gdi`.

- [ ] **Step 3: Create exact JSON configuration**

Store raw/percentile starts, score weights, EWMA alpha `0.5`, regime boundaries, prudent/symmetric thresholds, two-month persistence, staleness limits, source IDs, style satellite sizes `[0.05, 0.10, 0.15]`, and `shadow_inception = 2000-01` as fixed values. Decimal-like score values are JSON numbers because they are statistical—not fiscal—quantities.

- [ ] **Step 4: Implement small immutable result types**

```python
@dataclass(frozen=True)
class GdiComponents:
    gold: float
    money: float
    low_real_yield: float
    inflation_breadth: float
    gate: float
    repression: float
    pressure: float
    confirmed: float
    gdi: float

@dataclass(frozen=True)
class GdiMonth:
    decision_month: date
    status: GdiStatus
    components: GdiComponents | None
    gdi_ewma: float | None
    pressure_ewma: float | None
    confirmed_ewma: float | None
    prudent_state: PrudentState | None
    symmetric_state: SymmetricState | None
    source_months: tuple[tuple[str, str], ...]
    errors: tuple[str, ...] = ()
```

Validate score ranges, exact weight sum, ordered thresholds, and timezone-aware decision timestamps.

- [ ] **Step 5: Run focused and full-suite checkpoints**

Run GDI model tests, then all tests.

---

### Task 2: Institutional GDI source parsers

**Files:**
- Create: `perpetual_engine/gdi_sources.py`
- Create: `tests/test_gdi_sources.py`
- Modify: `config/data_sources_v1.json`

**Interfaces:**
- Produces: `parse_world_bank_gold()`, `parse_imf_sdr()`, `parse_country_money()`, `parse_oecd_gdp()`, `parse_oecd_cpi()`, `parse_weo_weights()`, `parse_real_yield()`.
- Consumes: frozen `SourceArtifact` and emits `ObservationRow`.

- [ ] **Step 1: Write the World Bank workbook sentinel test**

Build a workbook with sheet `Monthly Prices`, `BR5="Gold"`, `BR6="($/troy oz)"`, `A7="1960M01"`, and `BR7=35`. Assert the parser identifies the column from the two header cells, not physical column BR or a fictional `GOLD` workbook code.

```python
rows = parse_world_bank_gold(path, artifact)
self.assertEqual(rows[0].series_id, "WORLD_BANK_GOLD_USD_MONTHLY_AVG")
self.assertEqual(rows[0].value, Decimal("35"))
```

- [ ] **Step 2: Implement Gold and SDR parsers**

World Bank dates must match `YYYYMmm`; reject `Monthly Indices`, wrong unit, duplicate months, nulls, and history starting after 1996-01. Normalize IMF quote to `sdr_per_usd`; test reciprocal direction with `USD 1 = SDR 0.735500`.

- [ ] **Step 3: Write and implement exact money parser tests**

Test exact IDs/fields for FRED `M2SL`, ECB key `BSI.M.U2.Y.V.M30.X.I.U2.2300.Z01.A`, BOJ `MD02'MAM1YAM2M2MO`, BoE `LPMAUYN`/`RPMB53Q`, and PBOC label/unit. For PBOC, enforce priority `Money Supply attachment > Financial Statistics attachment > HTML` and select the latest eligible revision only.

- [ ] **Step 4: Implement the UK splice**

```python
link = full_m4[date(2009, 7, 1)] / m4ex[date(2009, 7, 1)]
level = full_m4[m] if m <= date(2009, 6, 1) else m4ex[m] * link
```

Both July observations are mandatory. Add tests for continuity, units, and the `UK_M4_TO_M4EX_SPLICE` label.

- [ ] **Step 5: Implement exact OECD selectors**

Reject any row that differs from the frozen GDP positional key:

```text
Q.Y.USA+EA+CHN+JPN+GBR.S1.S1.B1GQ._Z._Z._Z.PC.L.GY.T0102
```

For CPI, enforce the legacy key and Japan’s January-2019 COICOP-2018 switch; require exact equality in the overlap and sentinel Japan 2024-01 = 2.2 in the validation vintage.

- [ ] **Step 6: Implement WEO and real-yield parsers**

Accept the latest eligible official September/October autumn vintage, with the explicit legacy descriptor/alias map. Reject fuzzy country matches. Parse Cleveland `REAINTRATREARAT10Y` as monthly percent and preserve observation month separately from decision month.

- [ ] **Step 7: Add scale, missing-area, and staleness tests**

Reject decimal/percentage 100x errors, missing countries, negative/non-finite weights, stale rows, and weights not summing to one within `1e-12`.

- [ ] **Step 8: Run focused and full-suite checkpoints**

Run source tests, then all tests.

---

### Task 3: Monthly component mathematics and official state ledger

**Files:**
- Modify: `perpetual_engine/gdi.py`
- Create: `tests/test_gdi.py`

**Interfaces:**
- Produces: `midrank_percentile()`, `latest_eligible()`, `compute_gdi_history()`.
- Consumes: normalized Gold/SDR, money, GDP, WEO, CPI, and real-yield rows.

- [ ] **Step 1: Write percentile tests before implementation**

```python
def test_current_inclusive_midrank(self):
    self.assertEqual(midrank_percentile([1.0, 2.0, 2.0], 2), 100 * 2 / 3)

def test_history_starts_exactly_in_1997(self):
    result = component_percentiles(rows_from_1995_to_2000, percentile_start=date(1997, 1, 1))
    self.assertEqual(result[date(2000, 1, 1)].history_count, 37)
```

- [ ] **Step 2: Implement PIT selectors for each component**

Gold uses latest published World Bank month `k(t)` and exact `k(t)-12`. Money uses latest `m_i(t)` and exact 12-month predecessor. GDP, CPI, and real yield use latest eligible `q_i(t)`, `c_i(t)`, and `r(t)`. Every selected row is asserted eligible before calculation.

- [ ] **Step 3: Implement component equations exactly**

```python
gold_sdr = gold_usd_monthly_average * mean(sdr_per_usd_in_same_month)
em = money_yoy - real_gdp_yoy
money = sum(weight[i] * em_percentile[i] for i in AREAS)
inflation = sum(weight[i] * cpi_percentile[i] for i in AREAS)
low_real_yield = midrank_percentile(history_of_negated_yield, -real_yield)
gate = 0.5 * money / 100 + 0.5 * inflation / 100
repression = low_real_yield * gate
gdi = 0.40 * gold + 0.35 * money + 0.25 * repression
pressure = 0.60 * gold + 0.40 * low_real_yield
confirmed = 0.60 * money + 0.40 * repression
```

- [ ] **Step 4: Implement EWMA and state persistence**

Seed the first complete month with raw value and use alpha `0.5`. A missing month emits `DATA_INCOMPLETE`, resets EWMA/persistence, and the next complete month restarts with persistence one.

- [ ] **Step 5: Implement prudent then symmetric rules**

Assert one high month remains Neutral, two complete high months favor Value, Pressure-only remains Neutral, and two complete months at exactly 40 favor Growth only for the symmetric rule.

- [ ] **Step 6: Add formula reconciliation and no-look-ahead tests**

For every complete output, recompute all formulas from exported components and compare within `1e-12`. Shift a publication timestamp past the decision and assert the score either uses the prior row or becomes incomplete.

- [ ] **Step 7: Run focused and full-suite checkpoints**

Run GDI history tests, then all tests.

---

### Task 4: Arbitrary-day nowcast and weekly Gold/SDR Pulse

**Files:**
- Modify: `perpetual_engine/gdi.py`
- Modify: `perpetual_engine/gdi_sources.py`
- Create: `tests/test_gdi_nowcast.py`

**Interfaces:**
- Produces: `virtual_append_percentile()`, `gdi_nowcast()`, `gold_sdr_pulse()`.
- Consumes: official history through the prior month plus rows eligible at `as_of`.

- [ ] **Step 1: Write virtual-append and immutability tests**

```python
def test_virtual_append_tie(self):
    history = [10.0, 20.0, 20.0]
    expected = 100 * (1 + 0.5 * (2 + 1)) / 4
    self.assertEqual(virtual_append_percentile(history, 20.0), expected)

def test_nowcast_does_not_change_official_history(self):
    before = normalized_history_bytes(history)
    gdi_nowcast(as_of, history, inputs)
    self.assertEqual(normalized_history_bytes(history), before)
```

- [ ] **Step 2: Implement daily source grades**

Preferred daily Gold is frozen licensed LBMA. Fallback calls `yfinance.download("GC=F", auto_adjust=False, actions=False, repair=False)` only during refresh, freezes unadjusted `Close`, and labels it `PUBLIC_MARKET_PROXY_DIAGNOSTIC`.

- [ ] **Step 3: Implement exact-date Gold/SDR alignment**

Convert date-only availability to UTC, intersect exact common dates without carry, and reject stale windows. A public futures row can create only an `INDICATIVE_ONLY` provisional state.

- [ ] **Step 4: Implement the five-day Pulse and canonical comparison**

Use exactly five observations spanning at most ten calendar days. Prior year uses nearest-prior five observations without a future match. Scheduled change compares Friday 23:59:59 Europe/Rome with the preceding canonical Friday; an ad-hoc run reports null weekly change.

- [ ] **Step 5: Implement complete vs partial nowcast**

If all components are eligible, calculate a provisional composite and style state without persistence. If macro is missing but Gold is valid, emit `PARTIAL_NOWCAST`, suppress composite/state, and list unavailable components.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run nowcast/Pulse tests, then all tests.

---

### Task 5: Value/Growth indices, public proxy, EUR conversion, and proxy grade

**Files:**
- Create: `perpetual_engine/gdi_validation.py`
- Create: `tests/test_gdi_style_proxy.py`

**Interfaces:**
- Produces: `parse_french_style_proxy()`, `style_returns_eur()`, `validate_style_proxy()`.
- Consumes: Phase 2 FX, optional licensed MSCI Net USD returns, and frozen French archive.

- [ ] **Step 1: Write exact French-table tests**

Create a zip fixture with member `Developed_6_Portfolios_ME_BE-ME.csv` and multiple tables. Assert only `Average Value Weighted Returns -- Monthly`, `BIG LoBM` Growth, and `BIG HiBM` Value are selected and divided by 100 once.

- [ ] **Step 2: Implement labelled public style rows**

Emit `PUBLIC_DEVELOPED_BIG_GROWTH_TR_PROXY_USD` and `PUBLIC_DEVELOPED_BIG_VALUE_TR_PROXY_USD`; never use MSCI names for public data.

- [ ] **Step 3: Reuse Phase 2 EUR conversion exactly once**

```python
eur_return = (1 + usd_return) * fx_eur_per_usd_t / fx_eur_per_usd_t_minus_1 - 1
```

Assert Value and Growth share identical FX dates.

- [ ] **Step 4: Implement longest-common-segment proxy validation**

Choose the longest uninterrupted common segment, then latest end and latest start tie-breaks. Calculate Pearson correlation, OLS slope with intercept, sample tracking error, CAGR gap, and thresholds from §15.4.

- [ ] **Step 5: Add grade and unavailable-data tests**

Return `PROXY_VALIDATED`, `PROXY_NOT_EQUIVALENT`, or `VALIDATION_UNAVAILABLE`; failure never erases the public-proxy study or creates exact-MSCI claims.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run style-proxy tests, then all tests.

---

### Task 6: Forward validation, HAC, cohorts, and episode robustness

**Files:**
- Modify: `perpetual_engine/gdi_validation.py`
- Create: `tests/test_gdi_forward_validation.py`

**Interfaces:**
- Produces: `forward_spreads()`, `newey_west_favored_regression()`, `validation_table()`.
- Consumes: complete official state ledger and Value/Growth EUR returns.

- [ ] **Step 1: Write outcome-alignment tests**

Assert month `t` uses returns only from `t+1` through `t+h`, terminal incomplete horizons are dropped, and `DATA_INCOMPLETE`/`HOLD_DATA_INCOMPLETE` never enter any sample.

- [ ] **Step 2: Implement deterministic split and minimum sample**

First half contains `floor(N/2)`, second `ceil(N/2)`. Promotion becomes `INSUFFICIENT_SAMPLE` unless each favored-state half has at least 12 observations.

- [ ] **Step 3: Implement the fixed two-regressor HAC calculation**

For `Spread = alpha + beta*favored + error`, calculate OLS and Newey-West covariance with Bartlett weights, lag 11, and no small-sample correction. Use a 2x2 matrix helper inside this module; do not add `statsmodels`.

```python
weight_lag = 1 - lag / 12
```

Test against a hand-calculated short sentinel and a constant-spread zero-residual case.

- [ ] **Step 4: Implement episodes and leave-one-out**

Episode identity comes from the original complete monthly state ledger. Report count share and absolute-spread share; if total absolute spread is zero, all shares are zero. Promotion requires both mean and median to retain the required sign after removing every episode and at least two episodes.

- [ ] **Step 5: Implement twelve cohorts**

For each forward start-month `1..12` and state, report `N`, mean, median, and state-appropriate hit rate. Empty cohorts remain explicit rows.

- [ ] **Step 6: Freeze prudent promotion before symmetric results**

Run and serialize the prudent result first. The symmetric calculation reuses the exact code with Growth success defined by negative Value-minus-Growth mean/median and hit rate `P(spread<0)`.

- [ ] **Step 7: Run focused and full-suite checkpoints**

Run forward-validation tests, then all tests.

---

### Task 7: Fixed style-slot shadow portfolio comparison

**Files:**
- Modify: `perpetual_engine/backtest.py`
- Modify: `perpetual_engine/gdi_validation.py`
- Create: `tests/test_gdi_shadow_portfolio.py`

**Interfaces:**
- Produces: `run_gdi_shadow_comparison()`.
- Consumes: baseline Phase 2 ledger, GDI state ledger, style/broad-World returns, and Phase 2 cost config.

- [ ] **Step 1: Write inception and replacement tests**

At January 2000, relabel 10% of existing broad World as the Neutral style slot with zero new purchase cost. Assert total weight and accounting beta do not increase.

- [ ] **Step 2: Implement state-change trading only**

At the start of `t+1`, switch the entire drifted slot only when the accepted holding changes. A switch pays one sale and one purchase; unchanged or `HOLD_DATA_INCOMPLETE` pays zero GDI cost.

- [ ] **Step 3: Preserve drift and World-sleeve feasibility**

Never top the slot back to 10%. Return `FAIL_STYLE_SLOT_EXCEEDS_WORLD` if requested notional exceeds the unleveraged World sleeve.

- [ ] **Step 4: Run all frozen comparators**

Produce baseline, prudent 10%, symmetric 10%, and pre-declared 5%/15% sensitivities. Never pick a winner ex post and never alter another baseline parameter.

- [ ] **Step 5: Add cost and timing reconciliation tests**

Assert `pretrade NAV -> slot switch cost -> t+1 return`; style indices have no ETF TER until products are later selected.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run shadow-portfolio tests, then all tests.

---

### Task 8: Deterministic JSON/CSV reports and four-sheet workbook

**Files:**
- Create: `perpetual_engine/gdi_report.py`
- Create: `tests/test_gdi_report.py`
- Modify: `perpetual_engine/io.py`

**Interfaces:**
- Produces: `write_gdi_exports()`, `write_gdi_workbook()`.
- Consumes: completed monthly, nowcast, Pulse, validation, shadow, and manifest ledgers.

- [ ] **Step 1: Write failing schema tests**

Assert monthly exports contain raw/EWMA scores, all components, regime, persistence, both states, freshness, hashes, and evidence grade. Weekly export contains latest official month and Pulse, not a weekly official GDI.

- [ ] **Step 2: Implement normalized JSON/CSV output**

Sort rows by canonical date/state/source keys; use fixed field order for CSV and sorted compact JSON. Serialize nulls explicitly and non-finite floats as errors, never JSON NaN.

- [ ] **Step 3: Build exactly four workbook sheets**

Using `openpyxl`, create in order:

```text
GDI Dashboard
GDI History
Value Growth Validation
Data Vintages
```

Populate from exported ledgers only. Freeze header rows, use ISO dates, visible evidence/status columns, and no hidden calculation formulas.

- [ ] **Step 4: Add workbook reconciliation tests**

Reload the workbook and assert sheet names/order, row counts, boundary values, source hashes, and equality to CSV/JSON values. Verify workbook creation makes no network calls.

- [ ] **Step 5: Add byte-stability tests**

Write JSON/CSV twice and compare bytes. Workbook ZIP metadata can vary, so compare reloaded cell matrices and workbook properties fixed by the writer rather than raw XLSX bytes.

- [ ] **Step 6: Run focused and full-suite checkpoints**

Run report tests, then all tests.

---

### Task 9: Refresh integration, GDI CLI, end-to-end fixtures, documentation, and review gate

**Files:**
- Modify: `perpetual_engine/data_sources.py`
- Modify: `perpetual_engine/cli.py`
- Create: `tests/test_gdi_cli.py`
- Modify: `README.md`
- Create at runtime: `outputs/gdi_validation_v1/`

**Interfaces:**
- Produces the approved `gdi monthly`, `gdi nowcast`, and `gdi validate` commands.
- Extends `data refresh` with GDI sources while keeping it the only networked command.

- [ ] **Step 1: Add GDI sources to the existing refresh command**

Download official World Bank, IMF, central-bank, OECD, WEO, Cleveland/FRED, and French artifacts declared in config. Freeze raw bytes before parsing. Fetch `GC=F` only when the diagnostic source is requested. Preserve prior artifacts on any failure.

- [ ] **Step 2: Write offline CLI tests first**

Patch all network adapters to raise. From frozen fixtures, assert these commands succeed without network:

```text
python -m perpetual_engine gdi monthly --as-of 2026-07-31 --output DIR
python -m perpetual_engine gdi nowcast --as-of 2026-08-21T12:00:00+02:00 --output DIR
python -m perpetual_engine gdi validate --output DIR
```

- [ ] **Step 3: Implement the three subcommands**

`monthly` writes the official ledger through the requested close. `nowcast` writes provisional output without modifying monthly history. `validate` writes proxy grade, forward tables, cohorts, episodes, and shadow comparisons.

- [ ] **Step 4: Add failure-path end-to-end tests**

Corrupt a source hash, remove one country, reverse the SDR quote, stale Gold, and supply only 35 percentile observations. Each case must emit the named incomplete/failure state without renormalizing countries or creating a favored holding.

- [ ] **Step 5: Run deterministic fixture study twice**

Execute monthly and validation commands into two temporary directories. Compare normalized JSON/CSV file sets byte-for-byte and workbook cell matrices. Assert month-t state first changes month-t+1 shadow return.

- [ ] **Step 6: Update operator documentation**

Document refresh/offline separation, monthly official timing, arbitrary-day limitations, weekly Friday cutoff, evidence grades, Value/Growth proxy labels, four workbook sheets, and the rule that ETFs are selected only after this validation is frozen.

- [ ] **Step 7: Run final verification**

```powershell
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m unittest discover -s tests -v
rg -n "[T]BD|[T]ODO|implement later|optimi[sz]e|PUBLIC.*MSCI" perpetual_engine tests config README.md
```

Expected: clean dependency check, zero failures/errors, no placeholders, no optimizer, and no proxy mislabeled as MSCI.

- [ ] **Step 8: Independent implementation review**

Dispatch an agent who authored none of the implementation. Require it to read both approved specs, both implementation plans, every changed file, source manifests, and fresh test evidence. It must check PIT, formulas, no optimization, missing-data holding behavior, reporting auditability, and issue `PASS` or `FAIL` with file/line references. Any FAIL is corrected and re-reviewed before claiming completion.
