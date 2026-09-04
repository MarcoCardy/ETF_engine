# Chronos-2 Market Forecast v1.0

Date frozen: 2026-09-03. This is research software, not trading authority.

## Objective

Add a reproducible Chronos-2 research workflow that:

1. forecasts the next 12 monthly returns for `WORLD`, `MOMENTUM`, `QUALITY`, and `TREND` jointly;
2. conditions the forecast on three explicit future paths for the ECB deposit facility rate;
3. uses the ECB deposit rate, the US 10-year Treasury yield, Brent crude oil, a BIS global-liquidity proxy, and US headline inflation as historical covariates;
4. measures whether each covariate adds out-of-sample predictive value instead of assuming that more inputs are better;
5. preserves every prospective forecast and compares it with each realized monthly return as it becomes available;
6. permits later covariates only through an explicit parser, normalizer, validation, and contract revision.

The existing allocation, TCE, DRO, funding, and backtest behavior remains unchanged. Forecasts cannot place orders or alter portfolio weights.

## Frozen model and runtime

- Package: `chronos-forecasting==2.3.1`.
- Model: `amazon/chronos-2`.
- Model revision: `29ec3766d36d6f73f0696f85560a422f50e8498c`.
- Device: CPU.
- Prediction length: 12 monthly periods.
- Reported quantiles: `0.10`, `0.50`, and `0.90`.
- `transformers` remains constrained to `>=4.41,<5` because Chronos 2.3.1 is not reliable with all 5.x releases.
- Forecast, evaluation, reconciliation, and portfolio-report commands are offline. Only data-refresh commands may access the network.

The model must be loaded with the frozen revision. Offline execution fails clearly if that revision is absent from the local Hugging Face cache.

## Target series

The frozen v1 target source is `outputs/four_sleeve_v1/monthly_returns.csv`, validated against `outputs/four_sleeve_v1/manifest.json` before use. The required columns are:

- `WORLD`;
- `MOMENTUM`;
- `QUALITY`;
- `TREND`.

The initial input contains 240 complete monthly observations from `2006-06-30` through `2026-05-31`. Later runs may use the same manifest-validated artifact after the existing four-sleeve workflow appends newly completed months. Dates must be unique, strictly increasing month ends, and contiguous. Values must be finite decimal returns. Strategy-return columns in the same file are not targets or covariates. Every run binds the exact target-file and target-manifest hashes; revisions therefore create a new actual-data snapshot rather than silently altering earlier results.

The current target history is a frozen retrospective research reconstruction, not a point-in-time tradable index history. Every forecast output preserves that caveat. The forecast origin is always the last complete target month that also has valid covariate coverage; the initial origin is therefore `2026-05-31`.

## Covariates

All covariates use information available no later than the forecast origin. A row with a later `available_at` is excluded even if its observation date is earlier.

### ECB deposit facility rate

- ID: `ECB_DFR`.
- Official series key: `FM.D.U2.EUR.4F.KR.DFR.LEV`.
- Source: `https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata`.
- Unit: percentage points per annum.
- Monthly transformation: last rate effective on or before each month end.
- Role: historical covariate and known-future scenario covariate.

The effective date is the availability date because the official daily series records the rate applicable on that date.

### US 10-year Treasury yield

- ID: `US_TREASURY_10Y`.
- Official series: FRED `DGS10`, Market Yield on US Treasury Securities at 10-Year Constant Maturity, Quoted on an Investment Basis.
- Source: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10`.
- Unit: percentage points per annum, preserved as published; for example, `4.25` means 4.25%, not `0.0425`.
- Monthly transformation: arithmetic mean of the non-null daily yields in the month that pass the availability cut-off.
- Role: historical-only covariate.

The implementation reuses the project's existing FRED CSV parser and `treasury_next_business_day` rule. When an archived official release timestamp is unavailable, a daily observation becomes admissible on the next US federal business day at 23:59:59 UTC. No bond price, ETF return, duration transformation, or future US-yield path is substituted for the published percentage yield.

### Brent crude oil

- ID: `BRENT_RETURN`.
- Official upstream series: EIA Europe Brent Spot Price FOB.
- Retrieval series: FRED `DCOILBRENTEU` CSV at `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU`.
- Unit before transformation: US dollars per barrel.
- Monthly transformation: arithmetic mean of the daily observations in the month, followed by `log(mean_t / mean_t-1)`.
- Role: historical-only covariate.

For retrospective anti-hindsight handling, each daily observation is conservatively available seven calendar days after its observation date. This is intentionally slower than the typical publication schedule. The raw file, retrieval time, and SHA-256 are frozen so subsequent revisions cannot silently change a run.

### BIS global-liquidity proxy

- ID: `BIS_USD_CREDIT_YOY`.
- BIS dataflow: `WS_GLI`.
- Exact series key: `Q.USD.3P.N.A.I.B.771`.
- Meaning: year-on-year percentage change in US-dollar credit, bank loans plus debt securities, to non-bank borrowers located outside the United States.
- Source: `https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip`.
- Frequency: quarterly.
- Role: historical-only covariate.

This is labelled `BIS global-liquidity proxy`, never simply `global liquidity`: the BIS states that global liquidity is not directly observable and no single indicator is complete. A quarter is conservatively marked available four months after its quarter end. The last available value is carried across monthly rows until a later quarter becomes available. No interpolation between quarterly observations is allowed. BIS historical revisions are controlled by freezing the retrieved bytes and their SHA-256.

### US headline inflation

- ID: `US_CPI_YOY`.
- Official series: FRED `CPIAUCNS`, Consumer Price Index for All Urban Consumers: All Items in U.S. City Average.
- Source: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS`.
- Unit before transformation: index, 1982-1984 = 100, not seasonally adjusted.
- Monthly transformation: `100 * (CPI_t / CPI_t_minus_12 - 1)`.
- Role: historical-only covariate.

Each CPI observation is conservatively available at the end of the following calendar month. The workflow does not invent future inflation values and does not substitute core CPI for headline CPI.

## Normalized monthly contract

Refresh produces a frozen `covariates.csv` with exactly these columns:

```text
month,ECB_DFR,US_TREASURY_10Y,BRENT_RETURN,BIS_USD_CREDIT_YOY,US_CPI_YOY
```

`month` is an ISO month-end date. Every configured covariate column is numeric and finite for the target window. Missing, duplicate (including equal source/date observations from different artifact hashes), non-month-end, non-contiguous, or stale inputs fail the build; the workflow never fills arbitrary gaps. The only allowed carry is the documented stepwise carry for ECB rates and released quarterly BIS values.

Later covariates join this contract through one explicit parser/normalizer change plus a unique configured ID and normalized column. Configuration fails closed until that support exists. The forecasting and ablation loops then discover the enabled column without further model-specific code.

## Frozen data transaction

`chronos refresh` downloads all five covariate sources into a staging directory, validates and normalizes the complete set, hashes every raw and normalized artifact, and publishes a new immutable vintage only after all checks pass. A deterministic vintage ID binds the source URLs, raw hashes, availability rules, normalized hash, and configuration hash.

Published data lives under `data/chronos_v1/vintages/<vintage_id>/`. `data/chronos_v1/current_manifest.json` is replaced only after the new vintage validates. A failed download, parser, validation, or pointer replacement leaves the previously published vintage usable. Forecast and evaluation read only the manifest-selected frozen vintage and never download data.

## Chronos input construction

Each forecast item passed to `Chronos2Pipeline.predict_quantiles` has:

- `target`: a `4 x history_length` array ordered `WORLD`, `MOMENTUM`, `QUALITY`, `TREND`;
- `past_covariates`: the enabled monthly covariate arrays through the forecast origin, including headline US CPI year-on-year;
- `future_covariates`: only `ECB_DFR`, with 12 values from the selected rate scenario.

All items in a batch use identical target and covariate schemas. Chronos performs its native scaling; the workflow does not fit a scaler on the full sample. The returned shapes and finite values are validated before any output is published.

## ECB future scenarios

Let `r0` be the last ECB deposit rate available at the forecast origin. For horizon month `h` from 1 through 12:

- `ECB_FLAT`: `r(h) = r0`;
- `ECB_DOWN_100BP`: `r(h) = r0 - h / 12` percentage points;
- `ECB_UP_100BP`: `r(h) = r0 + h / 12` percentage points.

V1 freezes the magnitude at 100 basis points so the scenario names and identities remain exact. No zero floor is imposed because euro-area policy rates can be negative. These are transparent counterfactual paths, not forecasts of ECB decisions.

Every scenario produces a separate forecast for all four targets. The US Treasury yield, oil, BIS, and US CPI values are past-only; their unknown future values are never invented.

## Rate sensitivity and volatility diagnostics

The forecast report compares every target and horizon with `ECB_FLAT`. For each scenario it records the change in `q50` and in interval width `q90 - q10`. This exposes whether `WORLD`, `MOMENTUM`, `QUALITY`, and `TREND` respond differently as the assumed ECB path rises or falls without claiming that the scenario is an ECB forecast.

Chronos already observes volatility implicitly in the target history and expresses forecast uncertainty through its quantiles. V1 therefore does not duplicate trailing volatility as a model covariate. It reports trailing 12-month annualized realized volatility using sample monthly standard deviation times `sqrt(12)`, forecast interval width, and forecast errors grouped by low/middle/high realized-volatility tercile. Every trailing statistic uses only months available at its origin. Volatility becomes a candidate covariate only in a later version if a walk-forward ablation demonstrates incremental predictive value.

## Portfolio monitoring

Portfolio monitoring is descriptive and cannot place orders or change weights. It remains separate from the four Chronos targets.

The configured portfolio `P_US_FACTOR_TREND` is rebalanced to target weights monthly:

- 60% SWDA, iShares Core MSCI World UCITS ETF, ISIN `IE00B4L5Y983`;
- 15% QDVA, iShares Edge MSCI USA Momentum Factor UCITS ETF, ISIN `IE00BD1F4N50`;
- 15% QDVB, iShares Edge MSCI USA Quality Factor UCITS ETF, ISIN `IE00BD1F4L37`;
- 10% DBMG, iMGP DBi Managed Futures Fund R USD UCITS ETF, ISIN `LU2951555585`.

Returns are measured in EUR from adjusted accumulating-market prices. `SWDA.MI`, `QDVA.DE`, and `QDVB.DE` are EUR listings. `DBMG.L` is divided by 100 from GBp to GBP and multiplied by the last same-day-or-earlier `GBPEUR=X` quote, defined as EUR per GBP; price and FX observations must be no more than seven calendar days before month end. The first valid portfolio return month is the first month with two consecutive common component prices; DBMG is never backfilled before its 2025 launch. Monitoring reports monthly return, cumulative value, trailing 12-month annualized volatility, maximum drawdown, and rolling 12-month correlations. Monthly return, cumulative value, and drawdown are available immediately; only 12-month volatility and correlations remain unavailable before 12 returns. The short common history is displayed and no claim of stable DBMG decorrelation is made from it.

The earlier proposed portfolio `33% SWDA + 27% IWMO + 40% IWQI` remains deferred, not discarded: its exact `IWQI` listing or ISIN must be confirmed before real-price monitoring is enabled.

## Prospective forecast tracking

Every successful forecast is published once under `outputs/chronos_v1/forecasts/<forecast_id>/`. `forecast_id` is the SHA-256 of the model revision, configuration hash, target hash, covariate-vintage ID, forecast origin, and scenario definitions. An existing forecast directory is never overwritten. A repeated run with the same inputs reuses the original publication after verifying that its bytes match.

The first publication records `issued_at` from the UTC system clock. This is the only wall-clock field created by an offline forecast command and exists to distinguish genuinely prospective forecasts from calculations made after the outcome. Forecast values and their deterministic ID do not depend on `issued_at`.

After publishing a forecast, the command runs the same reconciliation used by `chronos reconcile`. Reconciliation scans all valid archived forecasts and the latest manifest-validated target history. For every forecast month whose target return is now known, it records:

```text
signed_error = actual_return - q50
absolute_error = abs(signed_error)
squared_error = signed_error ** 2
interval_hit = q10 <= actual_return <= q90
```

Each origin, horizon, scenario, and target remains a separate observation. Results are written as a new monitoring snapshot under `outputs/chronos_v1/monitoring/<monitoring_id>/`. `monitoring_id` is the SHA-256 of the actual target-data hash and the ordered included forecast-manifest hashes, so adding a forecast or a realized month always creates or reuses the correct snapshot. Prior snapshots and forecasts remain unchanged. Forecasts whose target month has not yet occurred are listed as pending and do not enter accuracy metrics.

The prospective track record is labelled `PROSPECTIVE_TRACK_RECORD` and is never mixed with the retrospective walk-forward evaluation. Live summaries report bias, MAE, RMSE, and 80% interval coverage by target, scenario, and horizon, plus a clearly labelled pooled view. A small sample count is always displayed; no accuracy conclusion is made from an unrevealed or empty sample.

## Walk-forward evaluation

Evaluation uses the last 36 monthly forecast origins for which all 12 realized target months exist. With the frozen target file, origins run from `2022-06-30` through `2025-05-31`. Each origin uses an expanding history ending at that origin and covariates whose `available_at` is no later than that origin.

Origins belonging to the same model variant are predicted in batches to keep CPU runtime practical. The model is loaded once per command. Evaluation uses the realized historical ECB path only in a separately labelled `ORACLE_FUTURE_RATE_UPPER_BOUND`; this variant measures conditional model capacity and is never counted as an implementable accuracy result. Operational comparisons use historical covariates only and do not leak realized future rates.

Metrics are reported for horizons 1, 3, 6, and 12 and pooled across all 12 horizons:

- median-forecast mean absolute error using `q50`;
- mean pinball loss across `q10`, `q50`, and `q90`;
- empirical coverage of the `q10`-`q90` interval.

The primary comparison metric is pooled mean pinball loss across targets, origins, horizons, and the three quantiles. A zero-return forecast is the required naive baseline. Chronos target-only and Chronos with all enabled covariates are both required comparators.

## Covariate contribution analysis

For `k` enabled covariates, evaluation runs at most `2k + 2` Chronos variants rather than all `2^k` subsets:

1. target-only base;
2. base plus each covariate individually;
3. full model with all covariates;
4. full model minus each covariate individually.

For each covariate, two loss reductions are reported; positive values mean improvement:

```text
standalone contribution = loss(base) - loss(base + covariate)
conditional contribution = loss(full - covariate) - loss(full)
```

The conditional contribution is the primary classification because it reveals whether a series still adds information after the other series are present. Standalone contribution is retained to expose redundancy and interactions.

A paired moving-block bootstrap operates on per-origin pinball-loss differences with block length six months, 2,000 resamples, deterministic seed 42, and a percentile 95% confidence interval. Classification uses the conditional contribution:

- `USEFUL` when the lower confidence bound is greater than zero;
- `HARMFUL` when the upper confidence bound is less than zero;
- `INCONCLUSIVE` otherwise.

No covariate is promoted merely because its point estimate is positive. Scenario forecasts remain available even when a covariate is inconclusive, but the report states the evidence plainly.

## Commands

The existing CLI gains one `chronos` command group:

```powershell
.venv\Scripts\python.exe -m perpetual_engine chronos refresh --config config\chronos_v1.json
.venv\Scripts\python.exe -m perpetual_engine chronos forecast --config config\chronos_v1.json --output outputs\chronos_v1
.venv\Scripts\python.exe -m perpetual_engine chronos evaluate --config config\chronos_v1.json --output outputs\chronos_v1_evaluation
.venv\Scripts\python.exe -m perpetual_engine chronos reconcile --config config\chronos_v1.json --forecast-root outputs\chronos_v1\forecasts --output outputs\chronos_v1\monitoring
.venv\Scripts\python.exe -m perpetual_engine chronos portfolio-refresh --config config\portfolio_p_v1.json
.venv\Scripts\python.exe -m perpetual_engine chronos portfolio-report --config config\portfolio_p_v1.json --output outputs\portfolio_p_v1
```

`refresh` and `portfolio-refresh` are the only networked commands. `forecast`, `evaluate`, `reconcile`, and `portfolio-report` validate hashes and run offline. `forecast` automatically reconciles earlier forecasts after publishing the new immutable forecast; the standalone command is available when target data are refreshed without generating a new forecast. Input errors return exit code 2 without publishing a partial output directory.

## Outputs

Each immutable forecast directory contains:

- `forecast.csv`: `scenario,target,forecast_month,horizon,q10,q50,q90` (144 rows);
- `scenario_sensitivity.csv`: `target,forecast_month,horizon,scenario,q50,flat_q50,q50_delta,interval_width,flat_interval_width,interval_width_delta` for ECB down/up versus flat;
- `manifest.json`: forecast ID, issuance time, model ID and revision, package versions, device, forecast origin, scenario definitions, research labels, configuration hash, input hashes, vintage ID, and hashes of generated files.

Evaluation output contains:

- `predictions.csv`: `variant,origin,target,forecast_month,horizon,actual,q10,q50,q90`;
- `metrics.csv`: `variant,target,horizon,mae_q50,mean_pinball_loss,interval_80_coverage`;
- `covariate_contribution.csv`: `covariate,comparison,horizon,loss_without,loss_with,improvement,ci_low,ci_high,classification`;
- `volatility_diagnostics.csv`: `origin,target,horizon,trailing_volatility_12m,volatility_tercile,q10,q50,q90,interval_width,actual,signed_error,absolute_error,interval_hit` for the `FULL` model only;
- `manifest.json`: the same reproducibility fields plus evaluation origins, bootstrap settings, and output hashes.

Volatility tercile thresholds are calculated separately for each target from the 36 trailing-volatility values in the retrospective evaluation and recorded in its manifest. They classify the report only and never enter model inputs or prospective decisions.

Portfolio monitoring output contains `portfolio_monthly.csv`, `portfolio_metrics.csv`, and `portfolio_correlations.csv`, with the exact component identifiers, weights, currency-conversion provenance, first common month, input hashes, and a `SHORT_LIVE_HISTORY` flag while fewer than 36 complete common monthly returns exist. Correlations include all six unordered component pairs, including DBMG versus each equity component.

Each prospective monitoring snapshot contains:

- `forecast_vs_actual.csv`: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90,actual,signed_error,absolute_error,squared_error,interval_hit`;
- `pending_forecasts.csv`: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90` for months without a realized target;
- `live_metrics.csv`: `scenario,target,horizon,count,bias,mae,rmse,interval_80_coverage`;
- `manifest.json`: monitoring ID, target-data and target-manifest hashes, included forecast-manifest hashes, snapshot label, and generated-file hashes.

Files are written into a staging directory and atomically published only when every forecast, comparison, metric, and hash succeeds. Repeated computations from identical model bytes, input bytes, and configuration produce identical forecast CSV bytes and the same forecast ID. The first publication time is preserved and is never rewritten by a later run.

## Failure rules

The command fails closed before publishing output when any of the following occurs:

- configured model revision is missing or cannot load offline;
- source, target, configuration, or normalized-data hash does not match its manifest;
- a required source or column is absent;
- dates are duplicated, out of order, non-month-end, or internally gapped;
- an input or prediction is non-finite;
- fewer than 36 fully evaluable 12-month origins exist;
- a future covariate is supplied for the US Treasury yield, oil, BIS liquidity, or US CPI;
- target or covariate ordering differs across batched items;
- an archived forecast manifest or forecast CSV does not match its recorded hash;
- a realized target month or target name cannot be matched uniquely to a forecast;
- reconciliation attempts to alter an existing forecast or monitoring snapshot;
- an output path escapes its requested output directory or collides with another artifact.

## Testing and verification

Implementation follows test-first development. One focused `tests/test_chronos.py` covers:

- target loading and manifest-hash validation;
- ECB, US Treasury, Brent, BIS, and US CPI transformations and their availability boundaries;
- monthly alignment without leakage;
- the three exact ECB scenario paths;
- per-target scenario sensitivity derived from the issued forecast without a second inference call;
- Chronos input shapes and covariate roles;
- rolling-origin boundaries and absence of future observations;
- pinball loss, block-bootstrap reproducibility, and the three contribution labels;
- origin-safe trailing volatility, target-specific retrospective terciles, interval widths, and error columns;
- deterministic forecast IDs and immutable repeat publication;
- prospective reconciliation, error formulas, pending forecasts, and live aggregation;
- fail-closed behavior and transactional output publication;
- CLI parsing without breaking existing commands.
- portfolio P weight/identifier validation, GBp-to-EUR conversion, first-common-month handling, and short-history metrics.

Unit tests use a deterministic tiny predictor at the model boundary so the ordinary test suite remains offline and does not require a 478 MB model download. Final verification additionally runs one cached-model CPU smoke forecast using the frozen Chronos revision, followed by the complete existing test suite.

## Explicit exclusions

- No fine-tuning or model-weight updates.
- No automated trading, allocation changes, or broker connection.
- No claim that conditional scenarios predict future ECB policy.
- No future US Treasury, Brent, or BIS scenario values.
- No future US CPI scenario values.
- No synthetic pre-launch DBMG history and no substitution of another managed-futures product for DBMG.
- No automatic selection of covariates after inspecting the same evaluation sample.
- No rewriting, deleting, or backfilling an issued prospective forecast after its outcome is known.
- No Shapley enumeration over all covariate subsets; the linear standalone and leave-one-out design is retained as covariate count grows.
- No use of the external disk for these small artifacts; the current project and model cache have sufficient space.
