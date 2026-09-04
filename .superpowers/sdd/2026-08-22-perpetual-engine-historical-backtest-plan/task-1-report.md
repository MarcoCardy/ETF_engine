# Task 1 Report — Gross-fiscal Rule E migration

## Status

DONE. This workspace is not a Git repository; no commits were created.

## Implementation summary

- Renamed the public policy config field to `protected_monthly_gross`, the policy-decision field to `target_gross_real`, and the target function to `target_gross_real()`.
- Changed the policy JSON contract to require `protected_monthly_gross`; a config containing only the legacy key now raises `KeyError('protected_monthly_gross')`.
- Kept the protected EUR 1,800 and NORMAL 3%-per-year/12 targets as gross fiscal amounts.
- Changed Funding Protocol G to use `gross_budget = min(target_gross_real, maximum_total_outflow_real)`. Cash, tax, commission, and spread all consume that single gross budget. Sales never increase to restore the delivered net amount.
- Updated deterministic JSON (via the dataclass field name), CSV `target_gross_real`, CLI output, config, and test helpers.

## TDD record

### RED

Command (run after changing tests and before production/config changes):

```powershell
.venv\Scripts\python.exe -m unittest tests.test_models tests.test_io tests.test_policy tests.test_funding tests.test_cli -v
```

Exit code: `1`

Observed output (failure summary):

```text
Ran 23 tests in 0.966s

FAILED (errors=15)
```

The expected missing-contract failures were present:

```text
AttributeError: 'PolicyConfig' object has no attribute 'protected_monthly_gross'. Did you mean: 'protected_monthly_net'?
ImportError: cannot import name 'target_gross_real' from 'perpetual_engine.policy'
TypeError: PolicyDecision.__init__() got an unexpected keyword argument 'target_gross_real'
KeyError: 'target_gross_real'
```

The remaining errors were the shared test helper constructing the renamed `PolicyDecision` field before the production dataclass had been migrated, as expected for this schema-breaking rename.

### Focused GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_models tests.test_io tests.test_policy tests.test_funding tests.test_cli -v
```

Output:

```text
----------------------------------------------------------------------
Ran 31 tests in 0.993s

OK
```

The focused run included and passed `test_protected_target_is_gross_fiscal`, `test_tax_reduces_net_without_gross_up`, `test_outflow_reconciles_to_net_tax_and_costs`, and legacy-config rejection.

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 31 tests in 0.947s

OK
```

## Files changed

- `perpetual_engine/models.py`
- `perpetual_engine/io.py`
- `perpetual_engine/policy.py`
- `perpetual_engine/funding.py`
- `perpetual_engine/cli.py`
- `config/policy_v1.json`
- `tests/helpers.py`
- `tests/test_models.py`
- `tests/test_policy.py`
- `tests/test_funding.py`
- `tests/test_io.py`
- `tests/test_cli.py`

## Self-review

- The budget is computed exactly as `min(target_gross_real, maximum_total_outflow_real)` using `Decimal` arithmetic.
- Cash uses part of the gross budget first; each sale then spends only the remaining gross budget on delivered net plus tax, commission, and spread.
- `total_outflow_real` remains exactly the sum of delivered net, tax, commission, and spread. The new taxable test asserts the reconciliation and confirms delivered net is below EUR 1,800 while total outflow is exactly EUR 1,800.
- The floor cap cannot be exceeded because `gross_budget` is capped before funding begins and every increment is bounded by `remaining_budget`.
- The legacy schema has no compatibility alias. A source search confirmed old names occur only in the explicit rejection test.
- JSON serialization inherits the renamed frozen dataclass field; CSV writes `target_gross_real` explicitly.

## Concerns

None.

## Fix round 1 — explicit legacy-key rejection

### Implementation summary

`load_config()` now fails immediately with `KeyError("protected_monthly_net is no longer supported; use protected_monthly_gross")` whenever the legacy field is present. This prevents a JSON object containing both old and new fields from silently accepting an ambiguous legacy value.

### TDD record

#### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_io -v
```

Exit code: `1`

Output:

```text
Ran 7 tests in 0.059s

FAILED (failures=2)
```

The new focused regression failed for the intended reason:

```text
FAIL: test_loader_rejects_legacy_key_when_gross_key_is_also_present
AssertionError: KeyError not raised
```

The existing legacy-only test also failed because the pre-fix loader raised the incidental missing-new-key error (`KeyError: 'protected_monthly_gross'`) rather than explicitly rejecting the legacy field.

#### GREEN — focused suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_models tests.test_io tests.test_policy tests.test_funding tests.test_cli -v
```

Output:

```text
----------------------------------------------------------------------
Ran 32 tests in 1.071s

OK
```

#### GREEN — full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 32 tests in 1.040s

OK
```

### Files changed in this fix round

- `perpetual_engine/io.py`
- `tests/test_io.py`
- `.superpowers/sdd/2026-08-22-perpetual-engine-historical-backtest-plan/task-1-report.md`

### Self-review

- The check is performed immediately after JSON loading and before any data is consumed, so it rejects both a legacy-only config and a config that includes both schema keys.
- The production change is one explicit membership check; no compatibility alias, migration, or alternate semantic path was added.

### Concerns

None.
