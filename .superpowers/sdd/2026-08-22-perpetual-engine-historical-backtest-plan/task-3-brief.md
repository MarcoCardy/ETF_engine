# Task 3 Brief — Damodaran ERP parser and release timing

Source plan: `docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md`  
Binding spec: `docs/superpowers/specs/2026-08-21-perpetual-engine-historical-backtest-design.md`, §§5–6, 10, 19–20.

## Files

- Modify: `perpetual_engine/data_sources.py`
- Create: `tests/test_damodaran.py`
- Create: `config/data_sources_v1.json`
- Modify: `requirements.txt` to add exactly `xlrd==2.0.2`.

## Ruling

The official annual source is legacy `.xls`; `openpyxl` cannot parse it. Use pinned `xlrd==2.0.2` directly for annual `.xls` only. Use `openpyxl` for monthly `.xlsx`. Do not add pandas or another Excel abstraction.

## Required interfaces

```python
def parse_damodaran_annual(path: Path, artifact: SourceArtifact) -> tuple[ObservationRow, ...]: ...
def parse_damodaran_monthly(path: Path, artifact: SourceArtifact) -> tuple[ObservationRow, ...]: ...
def damodaran_erp_asof(annual_rows, monthly_rows, decision_at: datetime) -> ObservationRow | None: ...
```

## Exact sources and fields

```text
annual_url = https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls
annual_field = Implied ERP (FCFE)
monthly_url = https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx
monthly_sheet = Historical ERP
monthly_date_field = Start of month
monthly_value_field = ERP (T12m)
```

- Reject any non-HTTPS URL or host/path differing from those exact URLs.
- Annual year `Y`: `observation_date=Y-12-31`; if archived publication time is absent, `available_at=Y+1-02-01 23:59:59 UTC`.
- Monthly row `Start of month=M`: exact observation month. If timestamp is absent, use first business day of M at `23:59:59 UTC`; it cannot affect the same month's opening allocation.
- Annual applies before September 2008. Monthly normalized source begins exactly September 2008.
- Normalize ERP to decimal: source `4.5` percent becomes `Decimal("0.045")`; source `0.045` remains `0.045` only when workbook unit/header and scale sentinel support that interpretation.
- Reject a normalized historical median below 1% or above 15%, nulls, nonnumeric values, duplicate dates, wrong sheets/columns, alternative ERP columns, and stale maximum date configured in `data_sources_v1.json`.
- Every emitted row carries artifact URL/hash/retrieval time and `CURRENT_VINTAGE_RESEARCH` unless true archived-release evidence is supplied.

## TDD sequence

1. Create annual `.xls` fixture with `xlrd`-readable bytes and monthly `.xlsx` fixture with `openpyxl`; write parser tests first.
2. Run focused RED for missing parser functions.
3. Implement the minimal exact selectors and run GREEN.
4. Add wrong-source/field/scale/timing tests first, observe RED, implement rejection/timing.
5. Run full suite once.

## Required sentinels

```python
def test_annual_2007_first_affects_march_2008(self):
    row = annual_row_for_2007
    self.assertEqual(row.available_at, datetime(2008, 2, 1, 23, 59, 59, tzinfo=timezone.utc))
    self.assertIsNone(damodaran_erp_asof((row,), (), utc("2008-02-01T12:00:00Z")))
    self.assertEqual(damodaran_erp_asof((row,), (), utc("2008-02-29T23:59:59Z")), row)

def test_monthly_switch_is_exact(self):
    self.assertEqual(monthly_rows[0].observation_date, date(2008, 9, 1))
    self.assertEqual(monthly_rows[0].series_id, "DAMODARAN_ERP_T12M")

def test_rejects_alternative_erp_column(self):
    with self.assertRaisesRegex(ValueError, "ERP \\(T12m\\)"):
        parse_damodaran_monthly(workbook_with_only_sustainable_erp, artifact)
```

Run focused: `.venv\Scripts\python.exe -m unittest tests.test_damodaran -v`  
Run full: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

## Constraints

- Use Task 2 `ObservationRow`, `SourceArtifact`, and `asof_select`; do not duplicate PIT logic.
- Network download remains outside parser functions.
- Use `apply_patch` for edits; do not initialize Git or dispatch subagents.
