# DRO Task 4 report

## RED

Added an end-to-end frozen-vintage fixture before the runner existed. The focused command failed at collection as intended:

```text
ModuleNotFoundError: No module named 'perpetual_engine.dro_report'
```

The test requires 80 published rows, 78 complete realized returns, a separately labelled July-to-August partial return, an August 28 provisional signal without a realized return, manifest binding validation, and byte-identical CSV/JSON/Markdown across two runs.

## GREEN

- Added `perpetual_engine/dro_report.py`: offline-only input validation, full-calendar enforcement, deterministic monthly CSV/JSON/Markdown publication, metrics, comparison series, calendar returns, selections and commission sensitivity.
- The end-to-end run exposed a frozen-contract defect: `MonthlyTotalReturnObservation` rejected the valid non-month-end `2026-08-28` partial observation. It now permits only that frozen partial date; all other observations remain month-end only.
- Added the minimal partial handling to the existing selector: the July signal can use the August 28 partial holding return, while the August 28 signal has no execution return.

Focused verification:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_dro.py -q
28 passed in 2.50s
```

Full verification:

```text
.\.venv\Scripts\python.exe -m pytest -q
221 passed in 67.50s
```

## Results

- Real study was run twice into `outputs/dro_v1`; the SHA-256 values matched for `monthly.csv`, `result.json`, and `report.md` on both runs.
- `monthly.csv` has 80 rows: 78 `COMPLETE`, `2026-07-31` labelled `PARTIAL_AS_OF_2026-08-28`, and `2026-08-28` labelled `PROVISIONAL_NEXT_EXECUTION` with blank realized-return fields.
- The complete realized period is February 2020 through July 2026. August is absent from complete-month metrics and calendar returns.
- Complete decisions had no empty eligible set and no cash fallback. Selection counts: EQAC 25, SGLD 22, WENE 22, EWSA 5, IWVL 2, DFNS 1, DTEH 1.
- Net DRO: CAGR `0.200465535144894518257357705`, annualized volatility `0.1925876501839515079105645113`, Sharpe `0.9744510238579892772724749612`, maximum drawdown `-0.2098966846123406091585321382`.
- SWDA comparator CAGR: `0.128314720850137799770124222`.
- Complete-period turnover was 39 legs and 10-bps modeled transaction cost was `0.039`; the result also contains the fixed €19 per-leg sensitivity for €5,000 and €80,000 notionals.

## Concerns

- These are retrospective conditional-index-proxy results, not tradable-ETF performance and not TCE-MA performance.
- The partial July holding observation is published for visibility but deliberately excluded from full metrics; the August 28 selection is prospective only.

## Correction — SWDA row return and survivorship label

### RED

Added assertions requiring `swda_benchmark_return` on every realized monthly row (including the July-to-August partial), a blank value for the August provisional row, mirrored selection history, and an explicit survivorship caveat in both JSON and Markdown. The focused test failed as intended with:

```text
KeyError: 'swda_benchmark_return'
```

### GREEN

- `monthly.csv` and `selection_history` now carry `swda_benchmark_return`, calculated over each row's signal-to-execution period. It is blank only when the provisional August signal has no execution period.
- `result.json` and `report.md` explicitly state that freezing the 2026 universe creates a survivorship limitation.
- No ranking, cost, return, or metric rule changed.

Verification:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_dro.py -q
28 passed in 5.84s

.\.venv\Scripts\python.exe -m pytest -q
221 passed in 176.66s
```

The regenerated `monthly.csv`, `result.json`, and `report.md` were byte-identical across two real runs.

## Integrity correction and report completion

### RED

Added adversarial frozen-vintage cases for future/stale/wrong-URL/invalid-timestamp benchmark and cash rows, candidate source injected into cash, source candidate identity drift, and raw path/size/hash/byte edits. The focused suite initially failed because altered benchmark metadata and raw-manifest entries were accepted.

### GREEN

- A supplied `vintage_root` now requires every manifest source to declare exactly `raw/<source_id>.csv`; the file must exist beneath `raw/` and reconcile by byte count and SHA-256. The no-`vintage_root` base-manifest case remains supported.
- Benchmark and cash rows now validate row hash and URL against the frozen manifest, bounded observation/availability dates, 45-day staleness, and timezone-aware retrieval timestamps. Cash accepts only the declared ECB source for the applicable regime.
- Manifest declarations reconcile with frozen configuration: declared official-NAV programmatic fallback URLs remain valid; FRED accepts only the configured landing URL or its exact frozen `fredgraph.csv?id=<ticker>` endpoint.
- The human report now includes net volatility/Sharpe/drawdown, cost model (10 bps per leg), €19-per-leg sensitivities at €5k/€80k, calendar returns, all four comparators, and all 80 monthly selection lines.

Verification:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_dro.py -q
30 passed in 8.26s

.\.venv\Scripts\python.exe -m pytest -q
223 passed in 170.16s
```

The final Markdown-only report completion was separately focused-verified:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_dro.py -q
30 passed in 8.09s
```

Two final real runs were byte-identical for `monthly.csv`, `result.json`, and `report.md`. No second full suite was launched after the Markdown-only completion, per parent direction.
