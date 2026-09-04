# DRO Task 3 report

## RED / GREEN

1. Added the vintage tests for canonical raw rows, last-available month-end normalization, exact coverage, staleness, USD/EUR same-month conversion, manifest hashes/final limits, and the 28 August partial limit. The focused command failed at collection because the new parser/normalizer/manifest functions did not exist.
2. Added the smallest validation helpers in `perpetual_engine/dro.py`; focused result: `24 passed`.
3. Added the per-row observation metadata test; it failed because `normalize_month_end_observations` did not exist. Added that helper and reran: `24 passed`.
4. Added the configuration test for frozen programmatic fallbacks, ECB cash inputs, actual FRED inceptions and the August 28 partial. It failed until the configuration was updated; focused result: `24 passed`.
5. Added a cash-input test proving the ECB deposit-facility predecessor is selected before €STR and €STR afterwards. It failed because `monthly_euro_cash_returns` did not exist. Added the input-only compounding helper; focused result: `25 passed`.

Fresh regression evidence:

```text
.\.venv\Scripts\python.exe -m pytest -q
exit code 0
```

## Frozen source coverage

GREEN — all 14 raw artifacts hash-reconcile with `data/dro_v1/manifest.json`.

- FRED: `NASDAQXNDXNNR` (actual inception 2011-10-10), `NASDAQNQUSB50201020N` (2012-12-03), and `DEXUSEU` (latest 2026-08-21, within the 45-day ceiling).
- Direct official LBMA Gold PM JSON: `https://prices.lbma.org.uk/json/gold_pm.json`, USD, converted with the same-month `DEXUSEU` fixing.
- Pre-return same-fund EUR adjusted-close fallbacks: `IS3S.DE`, `IUSN.DE`, `XDW0.DE`, `INFR.MI`, `CMOD.MI`, `IBCI.AS`, `DTLE.L`, and benchmark `SWDA.MI`.
- ECB cash inputs: €STR from 2019-10-01 and ECB deposit-facility predecessor beforehand. `monthly_cash_return_eur.csv` contains 93 explicit monthly inputs (10 predecessor, 83 €STR); no cash return is defaulted to zero.

RED — programmatic issuer NAV endpoint check returned HTTP 404 and the old LBMA webpage returned HTTP 403; neither is used in the frozen vintage. A direct Yahoo history-page check returned HTTP 429, while the declared `yfinance` history API successfully downloaded each pre-frozen fallback. No unavailable exposure was replaced after observing returns.

## Coverage / final limits

GREEN — a fresh offline audit verified:

- 10 candidate series × 93 observations: December 2018 through July 2026 plus `PARTIAL_AS_OF_2026-08-28`.
- SWDA benchmark: 93 observations in `monthly_benchmark_total_return_eur.csv`.
- EUR cash input: 93 observations in `monthly_cash_return_eur.csv`.
- Every normalized row records source ID, actual observation date, availability date, retrieval timestamp, URL and SHA-256; every observation date is on or before its labeled month.
- `strategy_performance_calculated` is `false`; no selector, strategy return, or Task 4 output was run.

## Files

- `config/dro_v1.json`
- `perpetual_engine/dro.py`
- `perpetual_engine/dro_refresh.py`
- `tests/test_dro.py`
- `data/dro_v1/raw/`
- `data/dro_v1/manifest.json`
- `data/dro_v1/monthly_total_return_eur.csv`
- `data/dro_v1/monthly_benchmark_total_return_eur.csv`
- `data/dro_v1/monthly_cash_return_eur.csv`
- `docs/superpowers/specs/2026-08-27-deterministic-ranking-overlay-design.md`
- `docs/superpowers/plans/2026-08-27-deterministic-ranking-overlay-plan.md`

## Review round 1 — normalized-file binding

### RED

Added a focused temporary-vintage test that requires the manifest to bind exactly these three relative files: `monthly_total_return_eur.csv`, `monthly_benchmark_total_return_eur.csv`, and `monthly_cash_return_eur.csv`. The test deliberately edits a byte and separately supplies a wrong path, row count, and SHA-256. It failed as expected with:

```text
TypeError: validate_vintage_manifest() got an unexpected keyword argument 'vintage_root'
```

### GREEN

- `validate_vintage_manifest` now reads each bound file from the supplied vintage root and verifies exact relative paths, byte count, SHA-256 and CSV row count.
- `freeze_dro_v1_manifest()` rebuilt only `data/dro_v1/manifest.json` from the existing frozen raw and normalized files; it did not make a network request or calculate performance.
- Focused command: `26 passed`.
- Full command: `.\.venv\Scripts\python.exe -m pytest -q` exited `0`.

Fresh manifest bindings:

- `monthly_total_return_eur.csv`: 930 rows, 212016 bytes.
- `monthly_benchmark_total_return_eur.csv`: 93 rows, 20535 bytes.
- `monthly_cash_return_eur.csv`: 93 rows, 23695 bytes.
