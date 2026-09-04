# Task 1 Brief — Correct Rule E to gross-fiscal targets

Source plan: `docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md`  
Binding spec: `docs/superpowers/specs/2026-08-21-perpetual-engine-historical-backtest-design.md`, especially §4 and §20 Gross Rule E.

## Files

- Modify: `perpetual_engine/models.py`
- Modify: `perpetual_engine/io.py`
- Modify: `perpetual_engine/policy.py`
- Modify: `perpetual_engine/funding.py`
- Modify: `config/policy_v1.json`
- Modify: `tests/helpers.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_policy.py`
- Modify: `tests/test_funding.py`
- Modify: `tests/test_io.py` and `tests/test_cli.py` only where the renamed serialized field requires it.

## Required behavior

- Rename `PolicyConfig.protected_monthly_net` to `protected_monthly_gross`.
- Rename `PolicyDecision.target_net_real` to `target_gross_real`.
- Rename `target_net_real()` to `target_gross_real()`.
- Rename JSON config key and CSV/JSON output field accordingly; reject the old JSON key rather than supporting two semantics.
- Both EUR 1,800 protected target and the 3% annual NORMAL target are gross fiscal targets.
- Funding may spend at most `min(target_gross_real, maximum_total_outflow_real)` on `delivered_net + tax + commission + spread`.
- Positive realized gain therefore makes delivered net lower than gross target. Never increase a sale to restore delivered net to the gross target.
- `total_outflow_real = delivered_net_real + tax_real + commission_real + spread_real`; floor cap is never exceeded.
- Preserve all unrelated policy/funding behavior and strict deterministic serialization.

## TDD sequence

1. Change/add tests first for the new names and gross behavior.
2. Run focused tests and record the expected RED failure caused by missing new fields/functions.
3. Implement the minimal rename and funding-budget semantic change.
4. Run focused GREEN tests and the complete suite once.

## Required tests

```python
def test_protected_target_is_gross_fiscal(self):
    result = target_gross_real(money("640000"), money("650000"), Lifecycle.DISTRIBUTION, self.config)
    self.assertEqual(result.target_gross_real, money("1800"))

def test_tax_reduces_net_without_gross_up(self):
    result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="5000"))
    self.assertEqual(result.total_outflow_real, money("1800"))
    self.assertLess(result.delivered_net_real, money("1800"))

def test_outflow_reconciles_to_net_tax_and_costs(self):
    result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="5000"))
    self.assertEqual(
        result.total_outflow_real,
        result.delivered_net_real + result.tax_real + result.commission_real + result.spread_real,
    )
```

Run focused: `.venv\Scripts\python.exe -m unittest tests.test_models tests.test_io tests.test_policy tests.test_funding tests.test_cli -v`  
Run full: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

## Constraints

- Use `Decimal` for fiscal arithmetic.
- Do not add compatibility aliases, migrations, optimizers, or dependencies.
- Do not touch historical backtest modules; this task is only the approved gross-fiscal migration.
- Do not initialize Git or dispatch subagents.
