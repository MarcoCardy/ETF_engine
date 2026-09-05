# Chronos-2 Direct ETF Dashboard v1.0

Date frozen: 2026-09-05. This is research software, not trading authority.

## Objective

Extend the existing local dashboard with a Chronos view that:

1. accepts exactly four confirmed ETFs and user-editable weights totaling 100%;
2. forecasts the real monthly EUR returns of those ETFs directly, rather than mapping them to the existing `WORLD`, `MOMENTUM`, `QUALITY`, and `TREND` proxy targets;
3. produces a forecast for the editable candidate portfolio while always retaining the immutable base portfolio as a visible reference;
4. applies the existing three ECB deposit-rate scenarios and the five validated macroeconomic covariates;
5. evaluates whether each covariate improves out-of-sample predictability, without assuming that adding a series is beneficial;
6. preserves each forecast and compares it with realized returns as new complete months become available;
7. performs network refreshes only after an explicit user action and performs the expensive contribution evaluation in a persistent background process.

This design adds a thin direct-ETF adapter and dashboard orchestration around the existing Chronos implementation. It does not duplicate the model engine, macro parsers, scenario rules, statistical metrics, or immutable-publication machinery already present in `perpetual_engine/chronos.py` and `perpetual_engine/chronos_data.py`.

## Non-goals and safety boundary

- The dashboard does not place orders, recommend trades, rebalance accounts, or alter portfolio weights from a forecast.
- A scenario is a transparent counterfactual assumption, not a forecast of an ECB decision.
- The system does not invent future values for US inflation, the US 10-year Treasury yield, Brent oil, or global liquidity.
- It does not synthesize ETF history before an ETF existed and does not substitute factor-proxy returns for missing ETF returns.
- It does not present a weighted sum of component quantile bounds as a statistically valid portfolio probability interval.
- The existing fixed four-sleeve Chronos CLI workflow remains supported and unchanged.

## Existing foundation reused

The feature reuses the frozen runtime and rules from `config/chronos_v1.json` and the existing Chronos v1 design:

- package `chronos-forecasting==2.3.1`;
- model `amazon/chronos-2` at revision `29ec3766d36d6f73f0696f85560a422f50e8498c`;
- CPU execution and the already cached model revision;
- a 12-month prediction horizon;
- quantiles `q10`, `q50`, and `q90`;
- immutable forecast, evaluation, and monitoring artifacts;
- origin-safe macroeconomic data and availability rules;
- walk-forward loss, paired moving-block bootstrap, and volatility diagnostics.

Forecasting, evaluation, reconciliation, and rendering are offline. The only network action in this view is the explicit **Aggiorna dati Chronos** command.

## Portfolio identities

### Immutable base portfolio

The reference portfolio is loaded from `config/portfolio_p_v1.json` and is always labelled **Portafoglio base**:

- 60% `SWDA.MI`, ISIN `IE00B4L5Y983`;
- 15% `IWMO.MI`, ISIN `IE00BP3QZ825`;
- 15% `IWQU.MI`, ISIN `IE00BP3QZ601`;
- 10% `DBMFE.PA`, ISIN `LU2951555403`.

The dashboard cannot overwrite this definition. A future application release may deliberately revise the versioned configuration, but a user edit cannot do so.

### Editable candidate portfolio

The **Portafoglio da studiare** contains exactly four unique, confirmed ETFs selected from the dashboard's saved ETF catalog. The user may enter a symbol or ISIN through the existing resolver and then set each weight. All weights must be finite, strictly positive, and sum to 100% within the existing dashboard tolerance before forecasting or evaluation is enabled.

The initial candidate equals the base portfolio. A valid example is `SWDA.MI`, `IWMO.MI`, `IWQU.MI`, and `GRID.MI` with user-selected weights. Replacing one ETF never alters the base portfolio.

The candidate is mutable local state stored atomically in:

```text
data/dashboard_v1/chronos_candidate_portfolio.json
```

It records the canonical symbol, ISIN when known, exchange, currency, display name, weight, and catalog identity used at confirmation. A symbol or ISIN must resolve unambiguously; unresolved, duplicate, delisted-without-data, or currency-incompatible entries fail visibly. The forecast never silently omits a component or renormalizes the remaining weights.

## Direct ETF target construction

The target set is the unique union of the four candidate components and the four base components. Overlapping ETFs are forecast once and reused in both portfolio calculations.

For every ETF, the adapter reads the manifest-selected adjusted-price history already maintained by the historical dashboard. It uses the canonical EUR listing selected in the catalog. Monthly prices and returns follow the dashboard's existing historical rules:

- the current incomplete calendar month is excluded;
- the selected month-end price is the last valid adjusted price on or before calendar month-end;
- that quote may be no more than seven calendar days stale;
- a monthly return requires valid prices for two consecutive accepted month ends;
- dates are unique and strictly increasing, and returns are finite decimals;
- no pre-listing backfill, proxy substitution, interpolation, or arbitrary gap filling is allowed.

Each target is bound to its canonical symbol, ISIN when known, exchange, currency, price-vintage manifest, source-file hash, and exact return-series hash. Those identities are carried into every output row and manifest so two listings with similar names cannot be confused.

### Common forecast origin

Candidate and base portfolio paths must be directly comparable. Their common origin is therefore the latest complete month for which every ETF in the union and all required historical covariates are valid. Each ETF may retain all of its own earlier observations up to that common origin; histories are not truncated to a common start date.

Every ETF must have at least 12 complete monthly returns ending at the common origin. If any required component fails this minimum, its reason is shown and the affected portfolio aggregate is not produced. No partial portfolio is displayed as if complete.

## History and reliability labels

Direct forecasting is allowed when an ETF has at least 12 complete monthly returns. Reliability is communicated separately from technical availability:

- 12 through 59 returns: forecast allowed with a prominent `STORICO_BREVE` warning;
- 60 or more returns: no short-history warning;
- fewer than 12 returns: `DATI_INSUFFICIENTI`, no forecast for that ETF, and no portfolio aggregate requiring it.

The warning is repeated beside the component forecast and portfolio result. It is not hidden in a download or tooltip.

Covariate contribution classification requires both at least 60 complete monthly returns and at least 12 usable walk-forward origins for every evaluated candidate ETF. If either condition fails, the result is `INSUFFICIENT_HISTORY`; the dashboard does not infer `USEFUL`, `HARMFUL`, or `INCONCLUSIVE` from an undersized sample.

## Macroeconomic covariates

The adapter uses the existing normalized, manifest-selected Chronos vintage under `data/chronos_v1/`. Historical inputs are:

- `ECB_DFR`: ECB deposit facility rate, percentage points per annum;
- `US_TREASURY_10Y`: published US 10-year Treasury yield in percent, not a bond price or decimal fraction;
- `US_CPI_YOY`: US headline CPI year-on-year percentage change;
- `BRENT_RETURN`: log return of the monthly mean Brent spot price;
- `BIS_USD_CREDIT_YOY`: BIS global-liquidity proxy.

All availability cut-offs, transformations, raw-data hashes, and validation rules remain those of the existing Chronos v1 contract. Data observed later than a forecast origin cannot enter its history.

Only `ECB_DFR` has a future path. Let `r0` be its last available value at the common origin and `h` range from 1 through 12:

- `ECB_FLAT`: `r(h) = r0`;
- `ECB_DOWN_100BP`: `r(h) = r0 - h / 12` percentage points;
- `ECB_UP_100BP`: `r(h) = r0 + h / 12` percentage points.

The other four covariates are past-only. They remain in the historical context but receive no fabricated future continuation.

## Forecast calculation

The direct-ETF adapter converts each validated ETF monthly-return series to the existing Chronos input contract and invokes the frozen model through the current model-loading and prediction code. It supports a dynamic target identity instead of changing the fixed four-sleeve target source.

For each unique ETF and ECB scenario, the output contains 12 forecast months with `q10`, `q50`, and `q90`. Forecasts refer to monthly decimal total returns in the listing currency and are never described as guaranteed outcomes.

For each scenario and horizon, the candidate portfolio central return is:

```text
portfolio_q50(h) = sum(weight_i * ETF_i_q50(h))
```

The displayed cumulative portfolio path starts from EUR 100 and compounds the weighted monthly `q50` returns. The base portfolio path is calculated in the same way from its fixed weights. The UI labels both as central scenario paths.

Component `q10` and `q90` values remain visible. The dashboard does not sum component quantiles and does not show a portfolio `q10`-`q90` band until a later method explicitly models joint residual dependence. Absence of that band is stated plainly, rather than replaced with false precision.

ECB sensitivity compares each down/up result with `ECB_FLAT` by ETF, forecast month, and horizon. It reports the change in `q50` and interval width. Portfolio sensitivity uses the weighted component `q50` differences only.

## Volatility treatment

Chronos already receives the ETF return history and expresses uncertainty through forecast quantiles, so trailing price volatility is not duplicated as a sixth covariate in this version.

The dashboard reports:

- trailing 12-month annualized realized volatility, using sample monthly standard deviation times `sqrt(12)`;
- forecast interval width `q90 - q10` for each ETF;
- retrospective errors grouped by ETF-specific low, middle, and high realized-volatility terciles when the evaluation sample is sufficient.

Every trailing statistic is computed using information available at its forecast origin. A volatility covariate may be considered later only if a walk-forward ablation can demonstrate incremental value.

## Explicit data refresh

**Aggiorna dati Chronos** is the sole network command in the Chronos dashboard view. It performs two existing refresh operations in sequence:

1. refresh the saved ETF price vintage used by the historical dashboard;
2. refresh the five Chronos macroeconomic sources and publish a validated macro vintage.

Each refresh retains its existing staging, validation, hashing, and atomic-publication boundary. If one fails, its previous valid vintage remains usable and the dashboard shows separate price and macro status; it never points to a partial vintage. Opening or reloading the local page does not contact the network.

The page displays model-cache status, ETF-price vintage status, macro-vintage status, last successful retrieval times, and validation errors. `Genera previsione` and `Valuta variabili` consume only the currently published local vintages.

## Immutable forecast publication

A successful direct-ETF forecast is published under:

```text
outputs/dashboard_chronos_v1/forecasts/<forecast_id>/
```

`forecast_id` is a deterministic SHA-256 over the model ID and revision, runtime configuration hash, ordered target identities and weights for both portfolios, ETF return-series hashes, price-vintage manifests, macro-vintage ID, common origin, and scenario definitions. An existing forecast is verified and reused; it is never overwritten.

The directory contains:

- `etf_forecast.csv`: `scenario,symbol,isin,exchange,currency,forecast_month,horizon,q10,q50,q90,history_count,history_status`;
- `portfolio_paths.csv`: `portfolio,scenario,forecast_month,horizon,central_return,cumulative_eur_100` for `CANDIDATE` and `BASE`;
- `scenario_sensitivity.csv`: component and portfolio `q50` deltas against `ECB_FLAT`, plus component interval-width deltas;
- `volatility_snapshot.csv`: latest origin-safe trailing volatility and forecast interval widths by ETF;
- `manifest.json`: forecast ID, first issuance time, exact portfolio definitions, model/runtime identity, common origin, scenarios, all input identities and hashes, warnings, research labels, and output hashes.

Files are produced in a staging directory and atomically published only after every component and aggregate validates. The first UTC `issued_at` is preserved on deterministic reuse.

## Separate background variable evaluation

**Valuta variabili** is separate from **Genera previsione** because the walk-forward ablation is much more expensive. It evaluates only the current candidate portfolio's ETFs and its weighted aggregate; the base portfolio remains the reference forecast trace and does not duplicate the ablation workload.

At each eligible origin, evaluation uses:

- an expanding ETF return history with at least 36 prior returns;
- only covariate observations available at that origin;
- all 12 subsequently realized returns;
- the last at most 36 eligible origins.

An ETF can receive a classification only when its full series has at least 60 returns and the common candidate evaluation has at least 12 usable origins. The candidate-portfolio metrics aggregate the component median forecasts and realized returns using the fixed candidate weights at every origin.

The evaluation reuses the existing bounded set of at most `2k + 2` variants for `k = 5` covariates:

1. target-only base;
2. base plus each covariate separately;
3. full model with all covariates;
4. full model minus each covariate separately.

It reports horizons 1, 3, 6, and 12 plus a pooled result. The primary loss is mean pinball loss across `q10`, `q50`, and `q90`; MAE of `q50` and empirical 80% interval coverage remain diagnostics. A zero-return forecast and Chronos target-only model remain required comparators.

For each covariate:

```text
standalone contribution = loss(base) - loss(base + covariate)
conditional contribution = loss(full - covariate) - loss(full)
```

The conditional contribution drives the label. A paired moving-block bootstrap over per-origin loss differences uses block length six, 2,000 resamples, seed 42, and a percentile 95% confidence interval:

- `USEFUL` when the lower bound is greater than zero;
- `HARMFUL` when the upper bound is less than zero;
- `INCONCLUSIVE` otherwise;
- `INSUFFICIENT_HISTORY` when the history or origin threshold is not met.

Positive point estimates alone never produce a favorable label.

### Persistent job lifecycle

Pressing **Valuta variabili** atomically creates an immutable evaluation request and launches one detached local worker process. The Streamlit page only observes its persisted state; closing the browser or dashboard does not cancel it.

There may be only one active evaluation. A second click while a verified job is running returns the existing job status instead of starting another. Mutable current status is stored atomically in:

```text
data/dashboard_v1/chronos_evaluation_job.json
```

It records the job ID, immutable request path, process identity, start time, heartbeat/update time, state, progress summary, result path, and sanitized failure message. Allowed terminal states are `SUCCEEDED`, `FAILED`, and `INTERRUPTED`.

On page startup, a job marked `RUNNING` is checked against its recorded local process. If the process no longer exists, such as after a computer shutdown, the dashboard atomically marks it `INTERRUPTED` and offers **Riprova valutazione**. A failed or interrupted request is never mistaken for a completed result. The background worker is offline and reads only the frozen paths and hashes named in its request, so a later refresh cannot mutate a running evaluation.

Completed output is published immutably under:

```text
outputs/dashboard_chronos_v1/evaluations/<evaluation_id>/
```

It contains `predictions.csv`, `metrics.csv`, `covariate_contribution.csv`, `volatility_diagnostics.csv`, and `manifest.json`, following the existing Chronos schemas with added ETF identity and `CANDIDATE_PORTFOLIO` rows where applicable. Progress state is not part of the immutable result.

## Forecast-versus-real tracking

After an explicit price refresh or a successful direct forecast, offline reconciliation scans all archived direct-ETF forecasts against the latest validated ETF return histories. For each forecast month now known, it records separately by forecast, scenario, ETF, and horizon:

```text
signed_error = actual_return - q50
absolute_error = abs(signed_error)
squared_error = signed_error ** 2
interval_hit = q10 <= actual_return <= q90
```

Candidate and base central portfolio errors are also computed from their archived weights and component returns. Months not yet realized remain pending and do not enter metrics. The prospective record is labelled `PROSPECTIVE_TRACK_RECORD` and is never merged with retrospective walk-forward results.

Each immutable monitoring snapshot is stored under:

```text
outputs/dashboard_chronos_v1/monitoring/<monitoring_id>/
```

It contains `forecast_vs_actual.csv`, `pending_forecasts.csv`, `live_metrics.csv`, and `manifest.json`. The manifest binds the actual ETF data hashes and included forecast manifests. Live summaries always show their observation count and do not make an accuracy claim from a small or empty sample.

## Dashboard behavior

The existing historical page remains the default and unchanged. The Chronos view adds the following flow:

1. **Stato**: cached-model, ETF-price, macro-vintage, and background-job status.
2. **Portafoglio base**: fixed composition and historical trace, always visible.
3. **Portafoglio da studiare**: four editable ETF selectors and weights, with explicit confirmation and reset to the base composition.
4. **Aggiorna dati Chronos**: the only network operation.
5. **Genera previsione**: foreground forecast from frozen local inputs, with clear progress and no data download.
6. **Valuta variabili**: detached background evaluation with persistent progress, result, failure, and retry state.
7. **Risultati**: per-ETF scenarios, candidate and base central paths, ECB sensitivity, history warnings, volatility diagnostics, covariate contribution, and prospective forecast errors.
8. **Download CSV**: forecasts, sensitivity, contribution, volatility, forecast-versus-real, pending forecasts, and provenance manifests.

Reloading the page reads saved portfolios, vintages, forecasts, monitoring snapshots, and job state. It does not refresh data, load the model, start a forecast, or start an evaluation automatically.

An unsupported component, invalid weight, stale/missing vintage, missing cached model, insufficient history, hash mismatch, or model failure is shown next to the blocked action. Prior valid artifacts remain readable.

## Failure and atomicity rules

No forecast or evaluation result is published when any required component fails. In particular, the workflow fails closed on:

- an unconfirmed or ambiguous symbol/ISIN;
- duplicate ETF identities or weights not totaling 100%;
- non-EUR or otherwise incompatible listing identity for this version;
- insufficient, non-finite, duplicated, non-monotonic, or stale price history;
- missing or invalid macro vintage;
- a requested model revision absent from the local cache;
- target, configuration, request, or manifest hash mismatch;
- non-finite or unordered quantiles;
- an incomplete component result needed for a portfolio aggregate;
- a second active evaluation request.

Staging directories may be cleaned after failure, but published artifacts are immutable. Mutable portfolio and job-state files use write-to-temporary-file followed by atomic replacement. User-facing failures contain a concise correction path and exclude secrets or raw stack traces.

## Verification

Implementation is not complete until all of the following pass:

### Automated checks

- direct monthly ETF targets obey incomplete-month and seven-day staleness rules;
- symbol/ISIN resolution, four-ETF uniqueness, and weight validation fail closed;
- base configuration cannot be mutated through candidate editing;
- overlapping base/candidate ETFs are forecast once and reused;
- the common origin and per-ETF history counts are correct;
- 11 returns block forecasting, 12 permit it with a warning, and 60 remove the warning;
- missing component forecasts block only the aggregates that require them and are never silently renormalized;
- portfolio `q50` and EUR 100 paths use the archived weights, while no aggregate quantile band is emitted;
- future values are supplied only for the ECB scenario covariate;
- contribution labels reproduce the seeded block bootstrap and insufficient history yields only `INSUFFICIENT_HISTORY`;
- prospective reconciliation computes signed error as actual minus `q50` and keeps future rows pending;
- immutable IDs change when any bound input changes and identical runs reuse verified artifacts;
- background jobs survive page closure, prevent duplicates, expose failures, detect an interrupted worker, and can be retried;
- opening or reloading the page performs no network request and starts no model work;
- offline dashboard tests render saved results and warnings without a live model.

Tests use a deterministic fake Chronos predictor except for the explicit real-model verification below.

### Real integration checks

- perform one explicit real ETF-price and macro refresh and verify both published manifests;
- run one real CPU Chronos forecast for an approved four-ETF candidate and verify all three scenarios, 12 horizons, base/candidate paths, warnings, hashes, and downloadable CSV files;
- run one real background contribution evaluation, record elapsed time and outcome, close and reopen the page while it runs, and verify the persisted result;
- refresh prices after at least one forecasted month is available and verify forecast-versus-real and pending rows;
- run the full existing test suite to confirm the historical dashboard and fixed four-sleeve Chronos CLI remain unchanged.

## Acceptance criteria

The feature is accepted when a non-technical user can open the local page, keep the base portfolio visible, define and confirm four saved ETFs by symbol or ISIN, explicitly refresh data, generate direct ETF and portfolio scenario forecasts, close the page while variable evaluation continues, reopen it to see progress or results, download the supporting CSV files, and later see predicted-versus-real errors—without any automatic network access, proxy substitution, hidden component omission, or trading action.
