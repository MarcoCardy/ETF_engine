# Perpetual Engine Reproducible Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, auditable Python implementation of real-capital accounting, Distribution Rule E, Funding Protocol G, and JSON/CSV weekly-report outputs for portfolio VI.

**Architecture:** Pure functions calculate policy state and funding proposals from immutable dataclasses using `Decimal`. File and command-line adapters remain outside the calculation modules. Versioned JSON fixtures preserve the original 2026-08-19 VI snapshot, while normalized JSON output and SHA-256 hashes provide reproducibility.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `decimal`, `enum`, `json`, `csv`, `hashlib`, `argparse`, `unittest`). No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-21-perpetual-engine-reproducible-core-design.md`

## Global Constraints

- Accounting currency is EUR and all money calculations use `Decimal`, never binary floating point.
- Base date is 2026-08-19; base real capital and initial HWM are EUR 614,590.86.
- Hard floor is EUR 600,000; activation requires explicit instruction and real capital of at least EUR 800,000.
- The ratchet adds EUR 50,000 for every completed EUR 100,000 of real HWM above EUR 614,590.86.
- E targets net real income; G includes tax, commission, and slippage when enforcing the total-outflow cap.
- ETF taxation uses ISIN-level weighted-average cost. The engine never selects fictional lots within one ETF position.
- Missing execution-critical data fails closed with operational payout zero.
- Tests must run without internet access.
- The workspace is not a Git repository. Replace commit steps with passing-test checkpoints; do not initialize Git without user authorization.

---

### Task 1: Domain types and validated input model

**Files:**
- Create: `perpetual_engine/__init__.py`
- Create: `perpetual_engine/models.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `money(value) -> Decimal`, `Lifecycle`, `DistributionState`, `TaxCategory`, `PolicyConfig`, `CpiObservation`, `Holding`, `PortfolioSnapshot`, `PolicyDecision`, `FundingTrade`, `FundingResult`, and `RunResult`.
- Consumes: no project code.

- [ ] **Step 1: Write failing validation tests**

```python
from decimal import Decimal
from unittest import TestCase

from dataclasses import replace

from perpetual_engine.models import Holding, PolicyConfig, TaxCategory, money


class ModelTests(TestCase):
    def test_money_uses_exact_decimal_text(self):
        self.assertEqual(money("614590.86"), Decimal("614590.86"))

    def test_snapshot_rejects_negative_quantity(self):
        with self.assertRaisesRegex(ValueError, "quantity"):
            Holding(
                symbol="SWDA", isin="IE00B4L5Y983", quantity=money("-1"),
                price_eur=money("129.22"), weighted_average_cost_eur=None,
                price_multiplier=money("1"), sale_increment=money("1"),
                tax_category=TaxCategory.ETF, tax_rate=money("0.26"),
                spread_bps=money("10"), commission_eur=money("19"),
                minimum_weight=money("0"), sale_permitted=True,
            )

    def test_config_rejects_floor_above_activation_threshold(self):
        with self.assertRaisesRegex(ValueError, "hard_floor"):
            replace(PolicyConfig.default(), hard_floor=money("900000"))
```

- [ ] **Step 2: Run the focused tests and confirm import failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_models -v`

Expected: `ModuleNotFoundError: No module named 'perpetual_engine'`.

- [ ] **Step 3: Implement the minimal immutable dataclasses and validation**

Use `@dataclass(frozen=True)` and string-valued enums. `money()` must construct `Decimal` from `str(value)` and reject non-finite values. Validate non-negative quantities, positive price multipliers and sale increments, tax rates in `[0, 1]`, and portfolio reconciliation within EUR 0.01.

```python
def money(value: str | int | Decimal) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("money must be finite")
    return result
```

Create `tests/helpers.py` with exact factories `holding(**overrides) -> Holding`, `snapshot(*, cash, cash_minimum, holdings=(), cpi_base=None, cpi_current=None) -> PortfolioSnapshot`, `taxable_snapshot(**holding_overrides) -> PortfolioSnapshot`, and `decision(*, target, cap, state=DistributionState.PROTECTED) -> PolicyDecision`. Each factory must start from one complete valid object and apply only the named overrides; later tasks use these helpers instead of duplicating large constructors.

- [ ] **Step 4: Run the focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_models -v`

Expected: all model tests pass.

- [ ] **Step 5: Run the complete test suite checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

---

### Task 2: Real-capital accounting, lifecycle, HWM, and Rule E

**Files:**
- Create: `perpetual_engine/policy.py`
- Create: `tests/test_policy.py`

**Interfaces:**
- Consumes: models from Task 1.
- Produces: `real_capital(nominal, cpi_base, cpi_current) -> Decimal`, `update_hwm(previous_hwm, pre_distribution_capital) -> Decimal`, `ratcheted_floor(hwm, config) -> Decimal`, and `evaluate_policy(snapshot, config, activation_date) -> PolicyDecision`.

- [ ] **Step 1: Write failing boundary tests**

```python
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.models import DistributionState, Lifecycle, PolicyConfig, money
from perpetual_engine.policy import ratcheted_floor, target_net_real


class PolicyTests(TestCase):
    def setUp(self):
        self.config = PolicyConfig.default()

    def test_ratchet_changes_only_on_completed_steps(self):
        self.assertEqual(ratcheted_floor(money("714590.85"), self.config), money("600000"))
        self.assertEqual(ratcheted_floor(money("714590.86"), self.config), money("650000"))
        self.assertEqual(ratcheted_floor(money("814590.86"), self.config), money("700000"))

    def test_growth_always_requests_zero(self):
        decision = target_net_real(money("900000"), money("700000"), Lifecycle.GROWTH)
        self.assertEqual(decision.state, DistributionState.GROWTH)
        self.assertEqual(decision.target_net_real, Decimal("0"))

    def test_protected_and_hard_stop_boundaries(self):
        protected = target_net_real(money("640000"), money("650000"), Lifecycle.DISTRIBUTION)
        stopped = target_net_real(money("599999"), money("650000"), Lifecycle.DISTRIBUTION)
        self.assertEqual(protected.target_net_real, money("1800"))
        self.assertEqual(protected.maximum_total_outflow_real, money("40000"))
        self.assertEqual(stopped.target_net_real, money("0"))

    def test_normal_target_is_three_percent_divided_monthly(self):
        decision = target_net_real(money("800000"), money("650000"), Lifecycle.DISTRIBUTION)
        self.assertEqual(decision.target_net_real, money("2000"))
```

- [ ] **Step 2: Run the policy tests and confirm missing-module failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_policy -v`

Expected: import failure for `perpetual_engine.policy`.

- [ ] **Step 3: Implement policy functions without I/O**

Use exact Decimal formulas from the spec. Quantize only user-facing money outputs to EUR 0.01 with `ROUND_DOWN` for caps so rounding can never breach a floor. Require activation date for `DISTRIBUTION`; reject activation below EUR 800,000.

- [ ] **Step 4: Add CPI failure tests and implementation**

```python
def test_missing_cpi_fails_closed(self):
    decision = evaluate_policy(snapshot_without_cpi(), self.config, activation_date=None)
    self.assertEqual(decision.state, DistributionState.DATA_INCOMPLETE)
    self.assertEqual(decision.target_net_real, money("0"))
    self.assertIn("CPI", decision.errors[0])
```

Run once before implementation to see the assertion fail, implement the guard, then rerun.

- [ ] **Step 5: Run the complete test suite checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

---

### Task 3: Funding Protocol G

**Files:**
- Create: `perpetual_engine/funding.py`
- Create: `tests/test_funding.py`

**Interfaces:**
- Consumes: `Holding`, `PortfolioSnapshot`, and `PolicyDecision`.
- Produces: `fund_withdrawal(snapshot, decision) -> FundingResult` and `marginal_sale(holding, units, first_trade) -> FundingTrade`.

- [ ] **Step 1: Write failing tests for the funding priority and cap**

```python
from unittest import TestCase

from perpetual_engine.funding import fund_withdrawal
from perpetual_engine.models import money
from tests.helpers import decision, snapshot, taxable_snapshot


class FundingTests(TestCase):
    def test_cash_above_minimum_is_used_first(self):
        result = fund_withdrawal(snapshot(cash="1000", cash_minimum="500"), decision(target="300", cap="300"))
        self.assertEqual(result.delivered_net_real, money("300"))
        self.assertEqual(result.cash_used_real, money("300"))
        self.assertEqual(result.trades, ())

    def test_total_outflow_never_exceeds_floor_buffer(self):
        result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="500"))
        self.assertLessEqual(result.total_outflow_real, money("500"))
        self.assertLess(result.delivered_net_real, money("500"))

    def test_missing_pmc_makes_asset_ineligible(self):
        result = fund_withdrawal(taxable_snapshot(weighted_average_cost_eur=None), decision(target="1000", cap="5000"))
        self.assertEqual(result.delivered_net_real, money("0"))
        self.assertIn("weighted_average_cost", result.errors[0])
```

- [ ] **Step 2: Run the tests and confirm missing-module failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_funding -v`

Expected: import failure for `perpetual_engine.funding`.

- [ ] **Step 3: Implement cash funding and one-instrument sale arithmetic**

For each sale increment calculate gross proceeds, positive embedded gain, tax, spread, one-time commission, and net liquidity. Excess sale proceeds remain portfolio cash. Total outflow equals delivered payout plus tax, spread, and commission.

```python
gross = units * holding.price_eur * holding.price_multiplier
gain = max(units * (holding.price_eur - holding.weighted_average_cost_eur) * holding.price_multiplier, ZERO)
tax = gain * holding.tax_rate
spread = gross * holding.spread_bps / Decimal("10000")
net_liquidity = gross - tax - spread - commission
```

- [ ] **Step 4: Write failing ordering and tax-category tests**

Add tests proving direct eligible government securities precede ordinary ETF sales when both respect minimum weights, lower marginal-cost ISINs precede higher-cost ISINs within one priority, and ETF losses are recorded but never deducted from ETF positive income tax.

- [ ] **Step 5: Implement deterministic greedy ordering**

Sort eligible holdings by `(funding_priority, marginal_tax_cost, spread_bps, symbol)`. Sell only complete `sale_increment` units. Before accepting each increment, ensure quantity stays non-negative, the configured minimum weight remains satisfied, and total outflow remains within the policy cap.

- [ ] **Step 6: Add reconciliation test**

Assert to EUR 0.01 that initial value minus final value equals delivered payout plus tax plus commission plus spread, after accounting for retained cash from sale-increment overshoot.

- [ ] **Step 7: Run the complete test suite checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

---

### Task 4: Versioned configuration and VI base fixture

**Files:**
- Create: `config/policy_v1.json`
- Create: `data/fixtures/vi_2026-08-19.json`
- Create: `perpetual_engine/io.py`
- Create: `tests/test_io.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces: `load_config(path) -> PolicyConfig`, `load_snapshot(path) -> PortfolioSnapshot`, `normalized_json(result) -> bytes`, `sha256_file(path) -> str`, and `write_summary_csv(result, path)`.

- [ ] **Step 1: Write failing fixture-loading tests**

```python
class IoTests(TestCase):
    def test_vi_fixture_preserves_original_total(self):
        snapshot = load_snapshot(Path("data/fixtures/vi_2026-08-19.json"))
        self.assertEqual(snapshot.nominal_capital_eur, money("614590.86"))
        self.assertEqual(snapshot.cash_eur, money("1950.90"))

    def test_normalized_result_is_byte_stable(self):
        first = normalized_json(sample_result())
        second = normalized_json(sample_result())
        self.assertEqual(first, second)
```

- [ ] **Step 2: Run tests and confirm missing files fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_io -v`

Expected: import or file-not-found failure.

- [ ] **Step 3: Create the exact policy configuration**

Store decimal values as JSON strings. Include base date, base capital, HWM, hard floor, activation threshold, ratchet steps, protected income, normal rate, and CPI series identifier.

- [ ] **Step 4: Create the VI fixture from the attachment**

Record all quantities, prices, values, and classifications shown in the attachment. Use JSON `null` for PMC or CPI observations not present in the source; do not infer them from displayed percentage returns. The expected shadow result is `DATA_INCOMPLETE` with operational payout zero until CPI is supplied.

- [ ] **Step 5: Implement strict JSON parsing and deterministic serialization**

Reject unknown enum values, inconsistent totals above EUR 0.01, duplicate ISINs, and non-string decimal fields. Serialize decimals as fixed strings, keys sorted, and compact separators.

- [ ] **Step 6: Run the complete test suite checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

---

### Task 5: Command-line evaluation and report outputs

**Files:**
- Create: `perpetual_engine/cli.py`
- Create: `perpetual_engine/__main__.py`
- Create: `tests/test_cli.py`
- Create at runtime: `outputs/latest/result.json`
- Create at runtime: `outputs/latest/summary.csv`

**Interfaces:**
- Consumes: config/snapshot loaders, `evaluate_policy`, and `fund_withdrawal`.
- Produces command: `python -m perpetual_engine evaluate --config PATH --snapshot PATH --out-dir PATH [--activation-date YYYY-MM-DD]`.

- [ ] **Step 1: Write failing end-to-end CLI test**

Use `tempfile.TemporaryDirectory` and `subprocess.run` to execute the module. Assert exit code zero for a valid but incomplete shadow snapshot, output files exist, state is `DATA_INCOMPLETE`, and operational payout is `0.00`.

- [ ] **Step 2: Run the CLI test and confirm failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_cli -v`

Expected: module or command failure.

- [ ] **Step 3: Implement the smallest CLI**

The command parses paths, loads inputs, evaluates E, calls G only when policy data are executable, writes normalized JSON and one-row CSV, and prints a short state summary. Malformed files return exit code 2; a valid `DATA_INCOMPLETE` result returns zero because it is a valid fail-closed policy outcome.

- [ ] **Step 4: Add malformed-input test**

Pass invalid JSON and assert exit code 2, no trade proposal, and an error message that names the invalid file without printing its contents.

- [ ] **Step 5: Run the VI fixture end to end**

Run:

```powershell
.venv\Scripts\python.exe -m perpetual_engine evaluate `
  --config config\policy_v1.json `
  --snapshot data\fixtures\vi_2026-08-19.json `
  --out-dir outputs\latest
```

Expected: operational payout zero, structured missing-CPI warning, hashes present, and no proposed sales.

- [ ] **Step 6: Run the complete test suite checkpoint**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

---

### Task 6: Documentation and final verification

**Files:**
- Create: `README.md`
- Modify: `requirements.txt` only if verification proves an undeclared runtime dependency; otherwise leave it unchanged.

**Interfaces:**
- Consumes: completed command and input formats.
- Produces: operator instructions for shadow evaluation and an explicit list of data required before payout execution.

- [ ] **Step 1: Document the reproducible command and safety model**

Explain `GROWTH`, `DISTRIBUTION`, `DATA_INCOMPLETE`, input hashes, why the base fixture cannot execute G without CPI and PMC, and how JSON/CSV feed the later workbook integration.

- [ ] **Step 2: Scan for forbidden ambiguity and accidental network coupling**

Run:

```powershell
rg -n "[T]BD|[T]ODO|implement later|yfinance|requests|urllib" perpetual_engine tests README.md
```

Expected: no placeholders and no network library imports in the core or tests. README may mention market data only as a later component.

- [ ] **Step 3: Run dependency verification**

Run: `.venv\Scripts\python.exe -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 4: Run all tests freshly**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

- [ ] **Step 5: Run the CLI twice and compare normalized outputs**

Run the VI command into `outputs/run_a` and `outputs/run_b`, then compare `result.json` byte for byte with PowerShell `Compare-Object` over `Get-Content -Raw`.

Expected: no differences.

- [ ] **Step 6: Record the completion checkpoint**

Report test count, dependency-check result, input hashes, output state, and the exact remaining missing data. Do not claim the historical backtest or Monte Carlo is complete; those are subsequent designs.
