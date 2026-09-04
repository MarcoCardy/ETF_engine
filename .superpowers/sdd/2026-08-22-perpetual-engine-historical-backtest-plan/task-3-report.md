# Task 3 report — Damodaran ERP parser and release timing

## Scope

Implemented only Task 3: offline Damodaran annual/monthly ERP parsers, exact source selection, normalization, point-in-time availability, and source freshness validation.

## RED / GREEN evidence

1. **RED — initial parser contract.** Added `tests/test_damodaran.py` with a locally embedded, gzip-compressed legacy `.xls` fixture (written to a temporary file before `xlrd` reads it) and an in-memory `openpyxl` monthly `.xlsx` fixture. Focused run failed as expected because `damodaran_erp_asof` and both parser functions were absent:

   ```text
   ImportError: cannot import name 'damodaran_erp_asof'
   ```

2. **GREEN — minimum interfaces and PIT timing.** Added the three required interfaces and exact selectors. Focused run: 3/3 passing.

3. **RED — malformed source/data coverage.** Added rejection coverage for wrong source, wrong sheet, alternative ERP column, null/nonnumeric values, duplicate/gapped dates, stale maximum observation, and decimal-scale handling. The focused run exposed two real failures: the missing internal-gap rejection and Windows temporary-file locks after a parser exception.

4. **GREEN — fail-closed validation and cleanup.** Added contiguous-month validation and closes the read-only monthly workbook in `finally`. Focused run: 12/12 passing. Full regression: 69/69 passing.

## Changed files

- `perpetual_engine/data_sources.py`
  - `parse_damodaran_annual(path, artifact)` via `xlrd` for the official legacy `.xls` annual file.
  - `parse_damodaran_monthly(path, artifact)` via `openpyxl` for the official `.xlsx` monthly file.
  - `damodaran_erp_asof(annual_rows, monthly_rows, decision_at)`, delegating all eligibility/tie-breaking to Task 2 `asof_select`.
  - Exact URL restrictions, schema/field validation, duplicate/gap/null/nonnumeric/scale/freshness rejection, `CURRENT_VINTAGE_RESEARCH` provenance through `ObservationRow`, and no network calls.
- `tests/test_damodaran.py`
  - Offline annual `.xls` and monthly `.xlsx` fixture tests; no network mocks or downloads.
- `config/data_sources_v1.json`
  - Damodaran source metadata and `monthly.max_staleness_days = 45`.
- `requirements.txt`
  - Added the required pin: `xlrd==2.0.2`.

## Exact official sources and selectors

| Series | Exact source / selector |
|---|---|
| Annual ERP | `https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls`; field `Implied ERP (FCFE)`; `Year` is converted to an `Y-12-31` observation. |
| Monthly ERP | `https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx`; sheet `Historical ERP`; date field `Start of month`; value field `ERP (T12m)`. |
| Normalized output | `series_id=DAMODARAN_ERP_T12M`, `unit=ratio`, with artifact URL, hash, retrieval timestamp, and `CURRENT_VINTAGE_RESEARCH`. |

## PIT and validation rules implemented

- The annual fallback is `Y+1-02-01 23:59:59 UTC`; annual 2007 is therefore absent before that instant and first usable for the March 2008 allocation.
- The monthly fallback is the first US federal business day of its observation month at `23:59:59 UTC`. September 2008 correctly resolves to 2008-09-02 because 2008-09-01 was Labor Day. A date-only monthly release cannot influence that month’s opening allocation.
- The annual series is eligible only before `2008-09-01T00:00:00Z`; on and after that switch, selection is monthly-only and fails closed when the monthly row is not yet available.
- `4.5` normalizes to `Decimal("0.045")`. A value already expressed as `0.045` is accepted only when the cell is percentage-formatted. The normalized historical median must be within 1%–15%.
- Monthly observations must start exactly at `2008-09-01`, be month starts, unique and contiguous; annual observations are also contiguous by year. The latest monthly date cannot be more than the configured 45 days behind `artifact.retrieved_at`.

## Assumptions

- `SourceArtifact` does not currently contain archived publication-time evidence, so parsers consistently use the approved conservative fallbacks rather than asserting an unavailable historical timestamp.
- Freshness is measured in calendar days from the latest monthly observation date to the UTC retrieval date, against the configured 45-day ceiling.
- Later Task 8 refresh code will consume this config and frozen artifacts; these parser functions deliberately remain offline and do not download anything.

## Fix round 1 — reviewer findings

Three Important findings were addressed with a new RED/GREEN cycle.

1. **Exact monthly inception.** RED: a workbook whose first row was October 2008 was accepted. GREEN: `parse_damodaran_monthly()` now requires its earliest observation to be exactly `2008-09-01`, before enforcing contiguous months.
2. **Fail-closed switch selection.** RED: the old as-of helper could return annual 2007 ERP after the September 2008 handoff when the monthly release was not yet available. GREEN: `select_damodaran_erp()` selects annual rows only before `2008-09-01T00:00:00Z`; on and after that instant it selects monthly rows only and returns `None` until a row satisfies `available_at <= decision_at`. `damodaran_erp_asof()` remains the Task 3 compatibility entry point and delegates to this fail-closed selector. Boundary tests cover `2008-09-01T23:59:59Z`, `2008-09-02T12:00:00Z` (before the fallback release), and `2008-09-02T23:59:59Z`.
3. **Annual gap rejection.** RED: annual years 2005 and 2007 were accepted. GREEN: `parse_damodaran_annual()` rejects any internal gap among pre-switch annual observations. The existing annual-2007 fallback test remains unchanged: it is available at `2008-02-01T23:59:59Z` and can first determine the March allocation.

Focused RED evidence after the new tests: the missing monthly-start and annual-gap checks failed exactly as expected. GREEN focused run: 15/15 passing; full regression: 72/72 passing.

## Verification

```text
.venv\Scripts\python.exe -m unittest tests.test_damodaran -v
15 tests, OK

.venv\Scripts\python.exe -m unittest discover -s tests -v
72 tests, OK
```
