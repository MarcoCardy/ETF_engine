# DRO Task 1 report

## RED

Command:

```text
.\\.venv\\Scripts\\python.exe -m unittest tests\\test_dro.py -v
```

Result: failed at collection with `ModuleNotFoundError: No module named 'perpetual_engine.dro'`.

## GREEN

Command:

```text
.\\.venv\\Scripts\\python.exe -m unittest tests\\test_dro.py -v
```

Result: `Ran 2 tests ... OK`.

Regression command:

```text
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q
```

Result: exit code 0. The suite emits expected `Input error` lines from CLI negative-path tests.

## Review round 1 — RED/GREEN

### RED

After adding negative tests for duplicate `(track, candidate_id)`, exposure drift, source-linked observations, EUR normalization, exact source/inception declarations, and a non-datetime `retrieved_at`, the focused command failed at collection:

```text
ImportError: cannot import name 'validate_monthly_observation' from 'perpetual_engine.dro'
```

### GREEN

Command:

```text
.\\.venv\\Scripts\\python.exe -m unittest tests\\test_dro.py -v
```

Result: `Ran 4 tests ... OK`.

The explicit DFNS caveat then followed its own TDD cycle: the focused suite failed with `AttributeError: 'SourceDefinition' object has no attribute 'coverage_caveat'`; after adding the `US_ONLY_PROXY` source-contract field and configuration entry, the same four tests passed.

Post-review regression command ` .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q` completed with exit code 0; its `Input error` output is expected coverage of CLI validation failures.

## Review round 2 — RED/GREEN

### RED

After adding tests for the independent frozen exposure map, exact v1 dates, FRED DEXUSEU FX provenance and USD-to-EUR division, the corrected DTLE proxy, and the LBMA gold exception, the focused command failed at collection:

```text
ImportError: cannot import name 'DRO_EXPOSURES' from 'perpetual_engine.dro'
```

### GREEN

Command:

```text
.\\.venv\\Scripts\\python.exe -m unittest tests\\test_dro.py -v
```

Result: `Ran 5 tests ... OK`.

Post-round regression command ` .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q` completed with exit code 0; its `Input error` output is expected coverage of CLI validation failures.

## Review round 3 — RED/GREEN

### RED

After adding the exact per-source `fx_conversion` and DTEH Acc share-class tests, the focused suite failed because `SourceDefinition` had no `fx_conversion` field; the generic conversion label therefore remained accepted.

### GREEN

Command:

```text
.\\.venv\\Scripts\\python.exe -m unittest tests\\test_dro.py -v
```

Result: `Ran 5 tests ... OK`. USD sources now require `DIVIDE_BY_USD_PER_EUR_DEXUSEU_AT_SAME_MONTH_END`; EUR sources require `NONE_EUR_BASE`.

Post-round regression command ` .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q` completed with exit code 0; its `Input error` output is expected coverage of CLI validation failures.

## Modified files

- `tests/test_dro.py`
- `perpetual_engine/dro.py`
- `config/dro_v1.json`
- `docs/superpowers/specs/2026-08-27-deterministic-ranking-overlay-design.md`
- `.superpowers/sdd/dro/task-1-report.md`

## Scope and open points

- Only Task 1 was implemented; no selector, accounting, download, or output work from later tasks was started.
- The configuration freezes source identities, declared inception dates, adjusted-total-return requirement, SHA-256 provenance, EUR conversion policy, and TCE separation. Task 3 must retrieve and validate the declared series, currencies, FX inputs, and dates; no network data were fetched here.
