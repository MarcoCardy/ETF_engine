# Task 4 brief — Developed-World proxy, FX, and validation

## Scope

Implement Task 4 only from the approved historical plan/spec. Use TDD and keep all tests offline with minimal frozen-like fixtures.

## Files

- Create `perpetual_engine/market_proxy.py`.
- Create `tests/test_market_proxy.py`.
- Modify `config/data_sources_v1.json` only for the exact Task 4 sources/fields.
- Create/update `task-4-report.md` in this ledger directory.

## Binding interfaces

- `build_public_world_monthly(...)`
- `convert_usd_to_eur(...)`
- `validate_world_proxy(...)`
- Frozen `ProxyMetrics(n, correlation, beta, tracking_error, cagr_gap, max_drawdown_gap, status)`.
- Reuse the existing PIT/provenance contracts; do not create a competing row type unless an internal immutable return record is strictly needed.

## Binding sources and construction

- International: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_International_Indices.zip`, member `Ind_all.Dat`, table `Value-Weight Dollar Returns`, exact column `Mkt`, reject `-99.99`, divide by 100 once.
- US: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip`, member `F-F_Research_Data_Factors.csv`, exact `Mkt-RF` and `RF`, `(Mkt-RF+RF)/100`.
- WDI: API root `https://api.worldbank.org/v2`, indicator `CM.MKT.LCAP.CD`, aggregates exactly `USA` and `WLD`; return year Y uses mandatory Y-2 caps and `w_US=USA/WLD`, held for the year, constrained to `[0,1]`, no interpolation.
- Reconstructed warm-up begins January 1977; reported history begins January 1979. Segment through June 1990 is `RECONSTRUCTED_SPLICE` (accept no alternate label). July 1990 onward uses `Developed_3_Factors.csv` from `Developed_3_Factors_CSV.zip`, return `(Mkt-RF+RF)/100`, segment `FF_DEVELOPED`.
- Canonical output label is exactly `PUBLIC_DEVELOPED_WORLD_TR_PROXY_USD`; the string `MSCI World` must not label the output.

## FX

- January 1977–December 1998: `CCUSSP01DEM650N`, fixed raw convention `USD_per_EUR`, normalize `EUR_per_USD=1/raw`, segment `SYNTHETIC_EUR_FX`.
- 1999 onward: `DEXUSEU`, fixed raw convention `USD_per_EUR`; month-end is final non-null fixing in month; normalize `1/raw`.
- December 2023 sentinel: raw near 1.105 and normalized near 0.905 within explicit narrow rounding tolerance.
- Monthly conversion: `(1+r_usd)*(fx_t/fx_prev)-1`. Direction sentinel: `convert_usd_to_eur(0.10, 1.0, 0.9) == -0.01`.
- Daily: reindex DEXUSEU onto underlying dates with latest prior fixing and fail if carry exceeds 7 calendar days. Do not double FX.
- No forward fill beyond last complete common historical month; live freshness <=45 days belongs in validation/config where relevant.

## Validation

- Official comparison source is iShares SWDA page `https://www.ishares.com/uk/individual/en/products/251882/SWDA`, base-currency USD NAV only, last published NAV per month, converted with the same month-end FX. Exchange prices are not substitutes.
- If official NAV is unavailable, status exactly `FAIL_MISSING_OFFICIAL_NAV`, never PASS/VALIDATION_UNAVAILABLE.
- Compare fee-adjusted proxy input `p` supplied by caller (0.20% layer is Task 6/7, do not silently apply it twice) against NAV EUR returns `n`.
- Use uninterrupted common monthly sample, no fill, minimum 60. Internal missing/nonfinite/fewer rows => `FAIL_INSUFFICIENT_NAV_SAMPLE`.
- Pearson correlation; OLS slope with intercept `Cov_sample(p,n)/Var_sample(n)`; TE sample std of `p-n` with ddof=1 times sqrt(12); CAGR product formula; absolute CAGR and maximum-drawdown gaps.
- PASS thresholds: correlation >=0.98, beta in [0.95,1.05], TE <=0.03, CAGR gap <=0.015. Otherwise status `FAIL`.

## Required RED tests before implementation

1. FX direction sentinel.
2. Exact splice: June 1990 reconstructed, July 1990 FF developed.
3. Warm-up January 1977–December 1978 and no gaps/duplicates January 1979 onward.
4. Reject French sentinel, wrong archive member/table/columns, percent scaling errors.
5. Mandatory WDI Y-2 values and weight bounds.
6. FX quote-direction/December-2023 sentinel/month-end selection; daily 7-day carry boundary.
7. Canonical label and no MSCI label.
8. Validation formulas, minimum uninterrupted sample, unavailable official NAV and threshold failures.

Run focused tests and the full suite. Report exact RED/GREEN commands/output, files changed, sources/fields and any assumptions. No network calls in tests, no optimization, no ETF claims, no other tasks.
