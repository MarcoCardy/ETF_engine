# Task 2 report: Covariate parsing and point-in-time monthly alignment

## Implementation summary

- Added strict standard-library parsers for the configured ECB deposit-rate CSV and BIS `WS_GLI` bulk ZIP. ECB values retain published percentage points and effective-date availability. BIS selects only `Q.USD.3P.N.A.I.B.771`, applies the four-month quarter-release lag, and permits monthly step carry only after release.
- Added a source-specific FRED wrapper that reuses `parse_fred_csv()`: DGS10 remains in raw published percentage points with Treasury next-business-day availability; Brent remains in raw dollars and receives a seven-calendar-day lag; headline `CPIAUCNS` remains a raw NSA index and receives an end-of-following-month lag.
- Added point-in-time monthly normalization for the exact ordered columns `ECB_DFR`, `US_TREASURY_10Y`, `BRENT_RETURN`, `BIS_USD_CREDIT_YOY`, and `US_CPI_YOY`. Treasury and Brent are never forward-filled; CPI requires the exact previous observation month and its 12-month comparison; only ECB and released BIS values carry.
- Extended the v1 configuration and exact source-role validation with historical-only FRED `CPIAUCNS` / `US_CPI_YOY`.
- Added raw-fixture regression coverage for all five sources, release boundaries, percentage scale, Brent log returns, CPI headline YoY, BIS bulk selection, malformed and duplicate data, missing months, finite output, order, shape, and immutability.

## RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v
```

Relevant failure:

```text
ImportError: cannot import name 'normalize_covariates' from 'perpetual_engine.chronos_data'
Ran 9 tests in 0.613s
FAILED (errors=9)
```

Why expected: the Task 2 parsers and normalizer did not exist. The focused tests therefore failed at the requested public interfaces before any production implementation was written.

During self-review, a second narrow RED cycle proved that a valid multi-series BIS bulk file was incorrectly rejected at its first unrelated series:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests.test_bis_rejects_unexpected_key_malformed_period_and_number -v
```

Relevant failure: `ValueError: BIS series key must be exactly Q.USD.3P.N.A.I.B.771`. This was expected after adding the regression: the bulk parser needed to filter unrelated rows while still rejecting an archive with no required series.

## GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v
```

Output:

```text
Ran 9 tests in 2.077s

OK
```

## Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -q
```

Result:

```text
Ran 240 tests in 171.593s

OK
```

The suite emitted its existing expected `Input error` diagnostics from fail-closed CLI tests; no failures occurred.

## Files changed

- `config/chronos_v1.json`
- `perpetual_engine/chronos_data.py`
- `tests/test_chronos.py`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-base/config/chronos_v1.json`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-head/`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-review.diff`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-report.md`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/progress.md`

## Self-review

- Confirmed all five source IDs, upstream series, parser names, and roles are exact and configuration order is retained.
- Confirmed DGS10 `4.20` and `4.30` normalize to `4.25`, not `0.0425`.
- Confirmed every monthly calculation filters on `available_at <= month-end 23:59:59 UTC`.
- Confirmed Brent uses `log(mean_t / mean_t_minus_1)` after its seven-day filter and rejects zero/non-finite normalized results.
- Confirmed CPI uses `100 * (CPI_t / CPI_t_minus_12 - 1)`, aligns only the exact monthly release available at the decision cut-off, and never substitutes core CPI.
- Confirmed BIS filters unrelated bulk-file rows, rejects a file with no exact required key, and performs no interpolation.
- Confirmed the normalized NumPy matrix is finite, ordered, correctly shaped, and read-only.
- No scenario sensitivity, volatility diagnostics, or portfolio logic was added.

## Concerns

- The Task 3 examples in the current implementation plan still say “four sources” and show availability/header examples that omit `US_CPI_YOY`. They must be revised before Task 3 so the refresh transaction includes the fifth raw source and its `observation_month_plus_1_end` availability rule.
- Commits: none (no repository).

## Fix round 1/5

### Implementation summary

- Added one shared normalization-boundary check that rejects duplicate `(series_id, observation_date)` values regardless of artifact hash; one subtest covers all five v1 sources.
- Replaced the permissive source count check with exact ordered equality against the five v1 IDs. An extra registered `past_only` source and reordered required sources now fail closed.
- Clarified the brief, specification, and plan: later covariates require explicit parser, normalizer, validation, and contract support.

### RED

Commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests.test_normalization_rejects_duplicate_dates_across_artifact_hashes -v
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests.test_load_chronos_config_rejects_invalid_required_source_mappings -v
```

Relevant failures:

```text
AssertionError: ValueError not raised
FAILED (failures=5)

AssertionError: ValueError not raised
FAILED (failures=2)
```

Why expected: `validate_rows()` treats artifact hash as part of observation identity, and configuration validation previously checked only required-source presence/count. Therefore cross-artifact duplicate dates, an extra registered historical source, and reordered required sources were all accepted.

### GREEN

Commands and output:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests.test_normalization_rejects_duplicate_dates_across_artifact_hashes -v
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests.test_load_chronos_config_rejects_invalid_required_source_mappings -v
```

Each command ran 1 test and returned `OK`.

Focused verification:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests tests.test_chronos.ChronosCovariateTests -v
```

```text
Ran 15 tests in 2.282s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -q
```

Result:

```text
Ran 241 tests in 152.277s

OK
```

The existing expected fail-closed CLI diagnostics were emitted; no failures occurred.

### Files changed

- `perpetual_engine/chronos_data.py`
- `tests/test_chronos.py`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-brief.md`
- `docs/superpowers/specs/2026-09-03-chronos-2-market-forecast-design.md`
- `docs/superpowers/plans/2026-09-03-chronos-2-market-forecast-plan.md`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-fix1-head/`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-fix1-review.diff`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-2-report.md`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/progress.md`

### Self-review

- The duplicate-date rule is enforced once for every source entering normalization and does not alter the broader point-in-time row identity used elsewhere.
- Exact tuple equality rejects missing, duplicate, extra, and reordered v1 sources before parser/normalizer dispatch.
- `scenario_basis_points == 100` remains frozen and asserted by the existing configuration test.
- No scenario, volatility, portfolio, or Task 3 implementation was added.

### Concerns

None. The earlier Task 3 CPI wording concern has been resolved in the current plan. Commits: none (no repository).
