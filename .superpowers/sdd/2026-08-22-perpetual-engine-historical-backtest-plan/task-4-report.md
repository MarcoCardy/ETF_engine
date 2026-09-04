# Task 4 report — Developed-World proxy, FX, and validation

## Scope

Implemented only Task 4: offline construction of the public developed-world USD return proxy, USD-to-EUR FX utilities, and deterministic proxy validation. No downloader, ETF recommendation, optimization, or later-task fee layer was added.

## RED / GREEN evidence

### RED

The required offline tests were added before production code.

```powershell
.venv\Scripts\python.exe -m unittest tests.test_market_proxy -v
```

Output before `perpetual_engine/market_proxy.py` existed:

```text
ImportError: Failed to import test module: test_market_proxy
ModuleNotFoundError: No module named 'perpetual_engine.market_proxy'
Ran 1 test in 0.001s
FAILED (errors=1)
```

### GREEN

Focused checkpoint:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_market_proxy -v
```

```text
Ran 8 tests in 0.260s
OK
```

Full checkpoint:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```text
Ran 80 tests in 1.783s
OK
```

`config/data_sources_v1.json` was also parsed successfully with `python -m json.tool`.

## Files changed

- Created `perpetual_engine/market_proxy.py`
- Created `tests/test_market_proxy.py`
- Modified `config/data_sources_v1.json` only to add the `market_proxy` source metadata
- Created this report

## Implemented contracts

- `build_public_world_monthly(inputs)` consumes local frozen `SourceArtifact` ZIPs and WDI cap values. It returns immutable records containing the shared `ObservationRow` contract plus the required segment.
- The reconstructed range starts in January 1977, uses mandatory calendar-year Y-2 WDI USA/WLD caps, rejects absent/out-of-range weights and French `-99.99`, and labels the pre-July-1990 segment `RECONSTRUCTED_SPLICE`.
- July 1990 is exactly the first `FF_DEVELOPED` monthly return. All output rows use `PUBLIC_DEVELOPED_WORLD_TR_PROXY_USD`; the implementation/config/test never use `MSCI World` as the output label.
- `convert_usd_to_eur()` implements `(1+r_usd) * (fx_t/fx_prev) - 1`; the fixed direction sentinel returns `-0.01` for `(0.10, 1.0, 0.9)`.
- `normalize_fx_monthly()` requires the explicit `USD_per_EUR` raw convention, selects the final non-null fixing per month, emits `EUR_per_USD=1/raw`, applies the January-1977–December-1998 synthetic segment, and enforces the December-2023 DEXUSEU sentinel. `carry_fx_daily()` fails after seven calendar days of carry.
- `validate_world_proxy()` does not apply any fee. It requires supplied fee-adjusted proxy returns and official NAV EUR returns, uses an uninterrupted monthly intersection of at least 60 observations, reports Pearson correlation, intercept OLS beta, sample-ddof-1 annualized TE, CAGR gap, and maximum-drawdown gap. Its statuses are exactly `PASS`, `FAIL`, `FAIL_MISSING_OFFICIAL_NAV`, and `FAIL_INSUFFICIENT_NAV_SAMPLE`.

## Exact sources and fields in configuration

| Input | Exact selector |
|---|---|
| International returns | `F-F_International_Indices.zip` / `Ind_all.Dat` / `Value-Weight Dollar Returns` / `Mkt` |
| US returns | `F-F_Research_Data_Factors_CSV.zip` / `F-F_Research_Data_Factors.csv` / `Mkt-RF`, `RF` |
| Developed returns | Monthly and daily official `Developed_3_Factors` archives / `Mkt-RF`, `RF` |
| WDI caps | API root `https://api.worldbank.org/v2`; `CM.MKT.LCAP.CD`; `USA`, `WLD`; lag 2 years |
| FX | `CCUSSP01DEM650N`, `DEXUSEU`; raw `USD_per_EUR`; normalized `EUR_per_USD`; carry ceiling 7 days; freshness ceiling 45 days |
| Official NAV comparison | iShares SWDA product page; USD base currency; final published NAV each month |

## Assumptions

- Task 8 will fetch/freeze the configured source artifacts; Task 4 performs no network I/O and tests use only tiny in-memory ZIP fixtures and rows.
- WDI cap observations enter through `PublicWorldInputs.wdi_caps`; their artifact identity is represented by the mandatory exact WDI API root, SHA-256 hash, and retrieval timestamp on the input/output provenance path.
- FX and NAV material is normalized to month-end keys for validation. This permits an official final NAV published before the literal final calendar day while still forbidding an internal missing calendar month.
- The 0.20% fee is deliberately absent here and must arrive once from Task 6/7 through the caller-supplied proxy-return series.

## Fix round 1 — provenance, PIT timing, FX carry, and metric regression coverage

### Review findings addressed

1. **Composite public-return provenance.** Added immutable `InputProvenance` records (`source_url`, UTC `retrieved_at`, SHA-256 hash). Every `PublicWorldReturn` now retains the exact effective inputs: international, US, and WDI for `RECONSTRUCTED_SPLICE`; developed only for `FF_DEVELOPED`. The row’s source URL is explicitly derived (`derived://public-developed-world/monthly-return`), its source hash is a deterministic SHA-256 over canonically sorted input references, and its retrieval time is their maximum.
2. **Outcome availability.** Every monthly return row has `available_at` at `23:59:59 UTC` on its observed month-end, while `retrieved_at` continues to represent the frozen current-vintage retrieval. The new as-of sentinel rejects the outcome one second before that instant and accepts it in the following month.
3. **Traceable FX.** `FXQuote` now carries both the normalized `EUR_per_USD` `ObservationRow` and the exact raw source fixing. `carry_fx_daily()` returns immutable `FXDailyCarry` records containing the selected quote and a simple `.value` accessor. It uses the raw fixing date for the seven-calendar-day ceiling.
4. **Deterministic selection.** Competing same-month fixing rows use the complete PIT ordering `(observation_date, available_at, retrieved_at, source_hash)`. Forward and reversed inputs now produce equal, byte-identical representations.
5. **Metrics.** Added an independent 60-month proxy-versus-NAV fixture with nonzero residuals and drawdowns. It calculates OLS beta, ddof=1 annualized tracking error, CAGR gap, and maximum-drawdown gap explicitly before comparing against `validate_world_proxy()`.

### RED

```powershell
.venv\Scripts\python.exe -m unittest tests.test_market_proxy -v
```

```text
ERROR: PublicWorldReturn object has no attribute 'input_provenance'
ERROR: FXQuote object has no attribute 'observation'
FAIL: month-end outcome available_at was 2026-08-22 instead of 1990-06-30T23:59:59+00:00
FAIL: same-month FX selection changed when input order was reversed
Ran 12 tests in 0.264s
FAILED (failures=2, errors=2)
```

The independent formulas test was intentionally already green: it confirmed the pre-existing metric formulas while adding regression coverage for unequal series.

### GREEN

```powershell
.venv\Scripts\python.exe -m unittest tests.test_market_proxy -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```text
Ran 12 tests in 0.302s
OK

Ran 84 tests in 1.657s
OK
```
