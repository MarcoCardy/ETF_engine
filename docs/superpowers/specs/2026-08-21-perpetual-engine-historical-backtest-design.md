# Perpetual Engine Phase 2 — Long-History Deterministic Backtest Design

Date: 2026-08-21  
Status: proposed for final user review  
Supersedes: the net-payout semantics in the Phase 1 core design

## 1. Purpose

Build an auditable historical allocation engine for the Perpetual Engine. It must test the Damodaran, Crisis/Recovery, and Treasury/TIPS rules over a long public-data history without presenting a public proxy as licensed MSCI data and without using information before it was available.

This phase also corrects Distribution Rule E: both the ordinary 3% annual withdrawal and the protected EUR 1,800 monthly withdrawal are **gross fiscal portfolio outflows**, not net amounts received by the investor.

The first Phase 2 deliverable is deterministic historical evidence. Monte Carlo, paired bootstrap, walk-forward optimization, Morin–Bayes updating, and a full historical Italian tax simulation remain later phases.

## 2. Binding user decisions

- Use the long-history public-proxy approach, starting in 1979.
- Obtain Damodaran ERP data only from Aswath Damodaran's official NYU site.
- Treat the ordinary 3% withdrawal and protected EUR 1,800 monthly withdrawal as gross fiscal amounts.
- Use an independent agent, not an implementation author, to review the final implementation and issue a PASS/FAIL report.
- Preserve the Phase 1 fail-closed and reproducibility principles.

The phrase “EUR 180000” in the approval is interpreted as a typographical rendering of the established EUR 1,800 monthly protected withdrawal. No EUR 180,000 withdrawal is introduced.

## 3. Scope and evidence grades

The engine produces two related but distinct studies.

### 3.1 Long monthly study

- Target period: January 1979 through the latest complete common month.
- Frequency: monthly.
- Purpose: test many valuation, inflation, crash, and recovery regimes.
- World asset: a documented public developed-world total-return proxy.
- Leveraged asset: a monthly approximation with explicit funding and residual-drag scenarios.
- Evidence grade: `LONG_PROXY_MONTHLY`.

### 3.2 Daily audit study

- Target period: July 1990 through the latest complete common day.
- Frequency: daily, summarized monthly.
- Purpose: verify daily leverage reset, path dependency, funding, calendar-day accrual, and FX treatment.
- Validation periods: IWDA/SWDA from 2009 and the official MSCI World Leveraged 2X Daily Net published history from February 2014.
- Evidence grade: `DAILY_PROXY_VALIDATION`.

Results from the monthly and daily studies must never be silently combined. Reports state the evidence grade next to every result.

## 4. Gross-fiscal Rule E correction

The existing names and semantics are changed as follows:

- `protected_monthly_net` becomes `protected_monthly_gross`.
- `target_net_real` becomes `target_gross_real`.
- NORMAL gross target: `real_capital * 0.03 / 12`.
- PROTECTED gross target: EUR 1,800 real per month.
- HARD STOP and GROWTH target: zero.
- Actual gross outflow is capped by the applicable real-capital floor buffer.

Protocol G does not sell additional assets to restore a net target. It minimizes tax and transaction costs within the fixed gross target:

```text
gross_target
  = net_received
  + realized_tax
  + commission
  + spread_or_slippage
```

If the gross target is EUR 1,800 and execution creates EUR 120 of tax and costs, the investor receives EUR 1,680. The engine must not increase the sale to deliver EUR 1,800 net.

The normal 3% rate is also gross fiscal. This migration is intentionally schema-breaking because retaining “net” field names would create an unsafe ambiguity.

## 5. Data-source policy

Network access is permitted only in a separate refresh command. A backtest run reads frozen local files and never downloads data.

Each raw artifact records:

- source name and exact URL;
- retrieval timestamp in UTC;
- HTTP metadata when available;
- SHA-256 content hash;
- parser version;
- first and last observation;
- whether it is official, academic, or validation-only;
- any license or redistribution limitation.

Derived files record their own hash plus the hashes of every raw parent. A changed source file creates a new data vintage; it does not overwrite the audit identity of an earlier result.

## 6. Damodaran ERP

Only these official sources are allowed:

- Annual ERP: `https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls`, field exactly `Implied ERP (FCFE)`
- Monthly ERP: `https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx`

The monthly parser selects sheet `Historical ERP`, date column `Start of month`, and value column exactly `ERP (T12m)`. It rejects sustainable-payout, smoothed, normalized, adjusted-risk-free, and net-cash-yield alternatives.

ERP is normalized internally as a decimal: 4% is `0.04`. Parser sentinels reject values whose scale would make the historical median implausibly below 1% or above 15%.

### 6.1 Annual availability before September 2008

An annual row for year Y is a year-end observation:

```text
observation_date = Y-12-31
```

If an official archived release timestamp is available, it becomes `available_at`. Otherwise the conservative fallback is:

```text
available_at = February 1 of Y+1 at 23:59:59 UTC
```

The fallback prevents an undemonstrated year-end value from affecting January or February returns. Under the month-end decision convention in section 10 it can first affect the March allocation.

### 6.2 Monthly availability from September 2008

The row labelled `Start of month = M` is the monthly observation for M. Its official release timestamp is used when recoverable. If only a release date is known, or no timestamp is recoverable, `available_at` is the first business day of M at 23:59:59 UTC. Consequently it can first affect the allocation for M+1; no same-day opening assumption is made.

The source workbook can revise historical rows. Therefore the workbook hash is part of every result and the series is described as a frozen research vintage, not an immutable historical tape.

## 7. Public developed-world total-return proxy

No output labels the public series as MSCI World. Its canonical name is:

```text
PUBLIC_DEVELOPED_WORLD_TR_PROXY_USD
```

### 7.1 January 1979 through June 1990

Construct a monthly splice from these frozen inputs:

- Kenneth French International Index Portfolios: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_International_Indices.zip`, member `Ind_all.Dat`, table `Value-Weight Dollar Returns`, column exactly `Mkt` (EAFE plus Canada), divided by 100;
- Kenneth French US factors: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip`, member `F-F_Research_Data_Factors.csv`, columns exactly `Mkt-RF` and `RF`, with `R_US=(Mkt-RF+RF)/100`;
- World Bank WDI market capitalization in current USD: indicator `CM.MKT.LCAP.CD`, API root `https://api.worldbank.org/v2`, aggregates exactly `USA` and `WLD`.

Kenneth French missing sentinel `-99.99` is rejected. For return year Y, the annual weights are frozen from Y-2 market capitalizations and held from January through December:

```text
cap_US,Y = WDI[USA, Y-2]
cap_world,Y = WDI[WLD, Y-2]
w_US,Y = cap_US,Y / cap_world,Y
w_non_US,Y = 1 - w_US,Y
R_proxy,m = w_US,Y * R_US,m + w_non_US,Y * R_non_US,m
```

Both USA and World Y-2 values are mandatory; there is no interpolation. Weights must lie in [0,1] and are held for the calendar year. A 1985 return therefore uses 1983 market caps. Because WLD includes markets not represented by the EAFE-plus-Canada return, this is an explicitly accepted weighting bias, not a country-matched reconstruction. The construction begins in January 1977 using 1975 weights; January 1977 through December 1978 is signal warm-up and reported performance starts in January 1979. This segment is labelled `RECONSTRUCTED_SPLICE`; WDI is a frozen current research vintage, not a verified historical vintage.

### 7.2 July 1990 onward

Use the Fama/French Developed Market return from the official files:

```text
monthly URL = https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_3_Factors_CSV.zip
monthly member = Developed_3_Factors.csv
daily URL = https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_3_Factors_Daily_CSV.zip
daily member = Developed_3_Factors_Daily.csv
developed_market_return = (Mkt-RF + RF) / 100
```

Use the monthly dataset for the long study and the daily dataset for the daily audit. The series is USD total return but is not identical to MSCI World Net Total Return. Changes in the Fama/French construction or underlying database are controlled by frozen hashes.

### 7.3 Validation against investable World exposure

From 2009 onward, compare the EUR proxy with official iShares IWDA/SWDA NAV total-return history exposed through the official product page `https://www.ishares.com/uk/individual/en/products/251882/SWDA`. Freeze the official base-currency USD NAV field, sample its final published NAV of each calendar month, and convert its USD return to EUR with the same month-end `DEXUSEU` factor used by the proxy. An already EUR-converted exchange price is not substituted. Exchange prices are a secondary diagnostic, not the primary validation target. Resolving and freezing the official USD NAV history is mandatory: if it is unavailable, the status is `FAIL_MISSING_OFFICIAL_NAV` and recommendations cannot be classified as validated.

Pre-registered validation thresholds are:

- monthly return correlation at least 0.98;
- regression beta between 0.95 and 1.05;
- annualized tracking error no more than 3.0%;
- absolute annualized CAGR gap no more than 1.5 percentage points;
- separately reported maximum-drawdown gap.

Use the uninterrupted intersection of calendar-month EUR total returns from the later first observation through the earlier final observation, with no filling and at least 60 observations. Let `p` be the investable World proxy after the single 0.20% fee layer in section 16 and `n` official NAV returns:

```text
correlation = Pearson(p, n)
beta = Cov_sample(p, n) / Var_sample(n)  # OLS p = alpha + beta*n + error
tracking_error = StDev_sample(p - n, ddof=1) * sqrt(12)
CAGR(r) = product(1+r)^(12/count(r)) - 1
max_drawdown(r) = min(wealth / running_max(wealth) - 1), wealth_0=1
```

Any internal missing month, non-finite return, or fewer than 60 common observations is `FAIL_INSUFFICIENT_NAV_SAMPLE`. CAGR gap means `abs(CAGR(p)-CAGR(n))`; maximum-drawdown gap means `abs(MDD(p)-MDD(n))`.

Failure of a threshold does not delete the backtest. It marks the World proxy validation `FAIL` and prevents portfolio recommendations from being classified as validated.

## 8. FX conversion

The long study converts USD returns to EUR using public end-of-period series. Both inputs have a frozen raw convention and transformation:

- January 1977–1998: FRED/OECD `CCUSSP01DEM650N`, `raw_quote=USD_per_EUR`, historical German observations in euro-equivalent units, normalized as `EUR_per_USD=1/raw`; segment label `SYNTHETIC_EUR_FX`. January 1977–December 1978 is warm-up only.
- 1999 onward: FRED `DEXUSEU`, `raw_quote=USD_per_EUR`, sampled at the final non-null observation of each calendar month and normalized as `EUR_per_USD=1/raw`.

The parser is not allowed to infer quote direction. A December 2023 `DEXUSEU` sentinel requires a raw observation near 1.105 and a normalized observation near 0.905; deviations beyond source rounding tolerance fail parsing. FRED/OECD `CCUSSP01EZM650N` is a validation-only monthly check through December 2023 and is never the live continuation source.

```text
R_asset_EUR,t
  = (1 + R_asset_USD,t)
    * (FX_EUR_per_USD,t / FX_EUR_per_USD,t-1)
    - 1
```

The OECD/FRED monthly-average series `CCUSMA02EZM618N` may be used only as a sensitivity check because an average rate is not aligned with close-to-close index returns.

The daily audit constructs daily USD leverage from July 1990. From 1999 it reindexes `DEXUSEU` to every underlying business date using the final fixing at or before that date. Carry is allowed for at most seven calendar days; a longer gap fails. From 1990 through 1998 it converts only the completed monthly leveraged USD return using the synthetic month-end EUR series. It applies FX once, after leverage, and never doubles the currency return.

These FRED/OECD files are frozen current-vintage outcome series. Their month-t values translate only the realized month-t return and are never allocation signals. They retain `retrieved_at` and a content hash. A live refresh must end no more than 45 calendar days before the refresh date; a historical run ends at the last complete common month instead of forward-filling FX.

## 9. Rates, inflation, and defensive return

- Treasury signal: FRED `DGS10`; for a month-t decision use the arithmetic mean of non-null daily observations in t-1 that pass the cut-off.
- TIPS veto: FRED `DFII10`, summarized with the same prior-month arithmetic mean; available from 2003 and absent before then rather than synthetically backfilled.
- USD leveraged funding: FRED `DFF` through 1985-12-31 as an explicitly labelled pre-LIBOR proxy; FRED `USD1MTD156N` from 1986-01-01 through 2021-08-31; and FRED `SOFR` from 2021-09-01. Values are annual percentages and accrue using actual calendar days divided by 360.
- EUR defensive proxy: FRED/OECD `IR3TIB01ITM156N` for the long study, labelled as an interbank-rate proxy rather than a BOT total-return index.
- Inflation deflator: FRED/OECD `ITACPALTT01IXNBM` through 2023-11, then Eurostat endpoint `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx?geo=IT&coicop=CP00&unit=I15`. Let `link=OECD_CPI[2023-11]/HICP[2023-11]`; use `HICP[t]*link` from 2023-12. Both November observations are mandatory and the splice must be continuous by construction. The series is labelled `ITALY_CPI_PROXY` because national CPI and HICP are not identical; the stale FRED mirror is never called a live current source.

For the defensive monthly return, select the latest annualized rate satisfying the decision cut-off and calculate:

```text
R_defensive,t = (1 + rate_decimal)^(days_in_month_t / 365) - 1
```

Rates at or below -100% and missing or stale observations are rejected. No future monthly average is used to price an already chosen allocation.

All economic data rows use the point-in-time contract in section 10. Revised current-vintage data are labelled as such unless true ALFRED/official vintages are present.

Availability normalization is source-specific:

- `DGS10` and `DFII10`: if an archived official release timestamp is absent, `available_at` is the next US federal business day at 23:59:59 UTC. The prior-month mean includes only daily observations passing this cut-off, so a month-end observation may be excluded from the immediately following decision.
- `IR3TIB01ITM156N`: an official archived release timestamp is used when available. Otherwise an observation for month M has `available_at` at 23:59:59 UTC on the final calendar day of M+1 and can first determine the return credited for M+2.
- `DFF`, `USD1MTD156N`, `SOFR`, CPI, and FX are realized return/deflation inputs, not allocation signals. They still carry frozen vintage metadata, but their month-t realized values cannot enter a month-t allocation decision.

No generic guessed release lag may replace these rules. Acceptance tests cover a month-end Treasury observation and an `IR3TIB` M-to-M+2 fallback.

## 10. Point-in-time data contract

Every normalized observation has:

```text
series_id
observation_date
value
available_at
source_url
retrieved_at
source_hash
vintage_status
quality_flags
```

Every allocation for month t has:

```text
decision_at = final calendar instant of month t-1, 23:59:59 UTC
execution_assumption = month-t opening allocation represented by the month-t close-to-close return
```

An as-of join may select only rows satisfying:

```text
available_at <= decision_at
```

Joining signals by observation date alone is forbidden. A dated release without an official intraday timestamp is normalized to 23:59:59 UTC on its release date. It cannot enter a decision made earlier that day; a first-business-day monthly release first affects the following month. A monthly return for month t is an outcome and cannot choose the allocation applied to that same return.

For month t, drawdown, momentum, and volatility use market observations completed by the end of t-1. Percentiles are expanding percentiles calculated only from values available at the decision date.

## 11. Signal definitions

For each monthly decision, market-derived signals use `PUBLIC_DEVELOPED_WORLD_TR_PROXY_EUR`, after the single FX conversion:

- `drawdown`: prior month-end proxy level divided by its prior running maximum minus one;
- `momentum_1m`: prior complete month's return;
- `momentum_3m`: compounded return over the prior three complete months;
- `volatility_12m`: sample standard deviation of the prior twelve monthly returns times square root of 12;
- `erp_percentile`: expanding midrank percentile against prior available ERP values;
- `tips_percentile`: expanding midrank percentile against prior available TIPS observations, after a minimum 36-month TIPS history.

For current value x and n strictly earlier admissible observations, the percentile is `(count(prior < x) + 0.5 * count(prior = x)) / n`. The current observation is not included in its reference distribution. No global full-sample percentile or normalization is allowed.

## 12. Allocation state machine

The baseline Damodaran beta target is:

- ERP below 3%: beta 0.60;
- ERP from 3% to below 4%: beta 0.95;
- ERP at least 4%: beta 1.00.

The no-trade band holds the previous beta when `abs(proposed_normal_beta - prior_accepted_final_beta) < 0.15`. Equality at 0.15 executes the change. It never operates on the ERP value itself and never blocks Crisis compression, Recovery ramping, or a TIPS-forced reduction.

### 12.1 Crisis

Crisis is active when all are true:

- drawdown at or below -5%;
- three-month momentum below zero;
- annualized 12-month volatility above 24%.

Phase 2 compresses beta to 0.80. The optional severe-crisis beta 0.60 is not introduced until it has a separately approved rule; this avoids fitting an unspecified threshold.

### 12.2 Recovery

Recovery is eligible when all are true:

- ERP is at least 4%;
- prior one-month return is positive;
- prior three-month momentum exceeds 1%;
- annualized volatility is below 32%;
- the market remains below its prior high.

During recovery, beta may rise by at most 0.10 per month. The normal cap is 1.00. A recovery cap of 1.20 is permitted only when the recovery conditions remain true and the TIPS veto does not impose a lower cap.

### 12.3 TIPS veto

- above the expanding 90th percentile: beta cap 1.00;
- above the expanding 95th percentile while ERP is below its expanding 60th percentile: beta cap 0.90;
- before TIPS data or before the minimum history: veto status `UNAVAILABLE`, with no synthetic cap.

When more than one condition applies, `tips_cap = min(all_applicable_caps)`; the 0.90 cap therefore wins the 90th/95th-percentile conflict.

Treasury 10Y remains a reported valuation and opportunity-cost variable. It does not independently drive beta.

### 12.4 Total precedence and transitions

The monthly algorithm is deterministic in this order:

1. Compute the raw ERP beta from the thresholds above.
2. If Crisis conditions are true, enter `CRISIS` and propose beta 0.80, overriding Recovery and the no-trade band.
3. Otherwise, if the prior state was `CRISIS` or `RECOVERY` and all Recovery conditions are true, enter or remain in `RECOVERY` and propose `min(prior_accepted_final_beta + 0.10, 1.20)`. The value is the prior month's final accepted signal beta after TIPS and the band, not actual portfolio beta after core-floor/no-borrowing constraints. If the conditions fail, enter `NORMAL` and propose the raw ERP beta.
4. If neither transition applies, remain or enter `NORMAL` and propose the raw ERP beta.
5. Apply the TIPS cap to the proposed beta. A cap reduction is mandatory.
6. Apply the 0.15 no-trade band only in `NORMAL` and only if no TIPS cap reduced beta; otherwise accept the proposed beta.

Crisis exits in the first month its conditions are false. Recovery has no hidden minimum duration: it continues only while every Recovery condition remains true. The saved ledger records raw beta, state proposal, TIPS cap, band decision, and final beta separately.

The January 1979 decision starts in `NORMAL`. There is no `prior_accepted_final_beta` at inception: the no-trade band is disabled and the raw ERP beta is accepted before any applicable TIPS cap. Signal warm-up uses the same frozen proxy from January 1977 through December 1978, which is excluded from reported performance. If all required 12 prior monthly returns are not present, Crisis and Recovery are `UNAVAILABLE` and cannot activate; the raw ERP beta still applies.

## 13. Beta-to-weight translation

At inception the unleveraged core is 60%. Thereafter its units are not bought or sold by the tactical rule, so its weight drifts with returns. A tactical trade is permitted only at inception or when accepted final beta differs from the prior accepted final beta, including Crisis, Recovery, and TIPS-forced changes. A no-trade-band `HOLD` means zero trades in both overlay and defensive sleeves; drift is not corrected. When a trade is permitted, let `core_weight_pretrade` be the current weight and solve:

```text
leveraged_overlay_target = max((final_beta - core_weight_pretrade) / 2, 0)
defensive_target = 1 - core_weight_pretrade - leveraged_overlay_target
```

Only overlay and defensive units are traded. If `final_beta < core_weight_pretrade` after the overlay reaches zero, the core creates a binding beta floor: actual beta equals `core_weight_pretrade` and the ledger records `CORE_FLOOR_BINDING`. If the formula would make the defensive weight negative, the overlay is reduced until weights sum to one and `NO_BORROWING_CAP` is recorded.

The overlay and defensive sleeves trade directly between their pre-trade and target values; no separate intermediate monthly rebalance is charged. The 60% value is thus an inception architecture, not a monthly reset that contradicts “do not sell the core.” Phase 2 requested beta never exceeds 1.20 and does not test Growth/surplus beta of 1.35–1.50. The fixed 60/20/20 comparator is rebalanced each January; all other months it drifts.

## 14. Leveraged World proxy

### 14.1 Long monthly approximation

The 1979 long study lacks daily developed-world returns before July 1990. For month t let `R_U,t` be the underlying USD monthly return, `days_t` its calendar days, and `rate_d` the frozen daily annual funding percentage. Funding is the sum of daily accruals, carrying the last prior business-day rate across weekends but never across the LIBOR/SOFR boundary:

```text
funding_t = sum((rate_d / 100) * calendar_days_to_next_observation_d / 360)
R_L_gross,t = 2 * R_U,t - funding_t
drag_month(d) = (1 - d)^(1/12) - 1
R_L_proxy,t = (1 + R_L_gross,t) * (1 + drag_month(d)) - 1
```

The primary missing-daily-reset residual is `d=0.011`; sensitivities are `0.006` and `0.015`. This residual exists only in the monthly approximation. If `R_L_gross,t <= -1`, the sleeve is set to -100%, flagged `LEVERAGED_SLEEVE_WIPEOUT`, and cannot recover without a new capital contribution. FX is applied once after the USD result.

The benchmark proxy above excludes ETF fees. When the investable LWLD layer is requested, apply its 0.60% annual expense multiplicatively as `(1-0.006)^(1/12)-1` after the proxy return. The residual and ETF charge are reported on separate lines; the residual represents missing daily-reset/replication behavior, not TER.

This is not described as the exact MSCI leveraged index and cannot validate daily reset behavior.

### 14.2 Daily audit proxy

From July 1990, construct the USD leveraged return daily:

```text
R_leveraged_USD,t
  = 2 * R_underlying_USD,t
    - (funding_rate_t / 100) * calendar_days_t / 360
```

Returns compound daily. Calendar days include weekends and holidays between observations. The final LIBOR observation governs only through 2021-08-31; SOFR governs from 2021-09-01. Missing rates are carried only from the latest prior business day for at most seven calendar days, otherwise the run fails. FX is applied once after leverage.

Daily compounding already creates volatility drag. No additional 1.1% volatility-drag deduction is allowed in the daily proxy. The 0.60% LWLD ongoing charge and any separately declared tracking residual form an ETF layer and are never confused with benchmark funding.

For the daily investable LWLD layer, if successive underlying observations are separated by `calendar_days_t`, accrue:

```text
fee_interval_t = (1 - 0.006)^(calendar_days_t / 365) - 1
R_LWLD_investable_EUR,t
  = (1 + R_leveraged_USD,t)
    * (FX_EUR_per_USD,t / FX_EUR_per_USD,t-1)
    * (1 + fee_interval_t)
    - 1
```

This fee-adjusted daily series, compounded to calendar months, is the only proxy compared with LWLD NAV. The pre-fee daily benchmark proxy is the only series compared with the MSCI leveraged index.

### 14.3 Validation

Validate the daily benchmark proxy before ETF TER from February 2014 against the official MSCI World Leveraged 2X Daily Net page `https://www.msci.com/indexes/index/760497/msci-world-leveraged-2x-daily-net-index` and the applicable official leveraged-index methodology. With licensed daily levels, use the uninterrupted common daily sample and require correlation at least 0.995, OLS-with-intercept beta 0.98–1.02, and sample tracking error no more than 2.0% annualized with square root of 252. With official summaries only, require at least five common calendar-year returns, absolute annual-return gaps no more than 3.0 percentage points each, and absolute 3/5/10-year annualized-return gaps no more than 2.0 percentage points wherever each horizon is published. Passing summaries yields `PASS_PARTIAL_OFFICIAL_SUMMARY`, never full daily validation.

Validate the fee-adjusted `R_LWLD_investable_EUR` against official base-currency USD LWLD NAV after the product's 2025 inception. Sample the final official NAV of each calendar month and convert its USD return with the same `DEXUSEU` month-end factor; if an official USD NAV field is unavailable, return `FAIL_MISSING_OFFICIAL_LWLD_NAV`. Use uninterrupted common monthly EUR returns and the same formulas as section 7.3. Require at least 12 observations, correlation at least 0.95, beta 0.85–1.15, annualized tracking error no more than 8%, and absolute CAGR gap no more than 5 percentage points. Any failed threshold is `FAIL_LWLD_VALIDATION`. Proxy failure prevents an exact-product claim but leaves stress-test results available as proxy evidence.

## 15. Compared strategies

The deterministic report compares the same monthly return history for:

1. unleveraged developed-world proxy benchmark;
2. fixed 60/20/20 architecture;
3. Damodaran-only beta engine;
4. Damodaran plus Crisis/Recovery;
5. complete Phase 2 engine with TIPS veto.

The primary Phase 2 run is accumulation/GROWTH and therefore has zero distributions. Rule E gross semantics are corrected and tested in the core, but a full historical tax-and-distribution simulation is deferred to the paired bootstrap/Monte Carlo phase.

## 16. Costs and tax boundaries

The primary deterministic cost configuration is:

```text
initial_capital_eur = 800000
commission_per_executed_sleeve_order_eur = 19
spread_slippage_bps = 10
```

Commission is charged once for each sleeve with a non-zero executed trade. Spread/slippage is applied to traded notional. Sensitivities replace 10 bps with 5 and 20 bps. Results always state whether capital is nominal-model EUR or inflation-adjusted EUR; costs use the same ledger unit and are never silently rescaled.

Every month uses this accounting order:

1. Start with sleeve values after the preceding month's returns: `pretrade_NAV`.
2. Compute gross target notionals from `pretrade_NAV`; targets are not re-solved after costs.
3. For each sleeve, set `trade_i = target_i - pretrade_i`. A non-zero trade pays `commission_i=19` plus `spread_i=abs(trade_i)*spread_bps/10000`.
4. Set the sleeve's post-trade, pre-return value to `target_i - commission_i - spread_i`. Each traded sleeve bears its own costs; costs are not shifted to the core or another sleeve. A negative result fails `COST_EXCEEDS_SLEEVE_VALUE`.
5. Recompute total NAV and diagnostic weights after costs, then apply month-t sleeve returns to those remaining values.

The January 1979 initial deployment is treated as a trade from cash and pays commission/spread for each non-zero sleeve. The unleveraged benchmark pays one initial order and then holds. The fixed 60/20/20 comparator pays all non-zero initial orders and all non-zero sleeve trades at its January rebalances. Tactical strategies pay only on the trigger events defined in section 13. Zero-value numerical noise below EUR 0.01 is rounded to zero before deciding whether an order exists; all other accounting retains full precision until output serialization.

The primary strategy run is the investable-cost layer. For every month, after the single USD-to-EUR conversion, apply the unleveraged World expense exactly once:

```text
world_fee_month = (1 - 0.002)^(1/12) - 1
R_world_investable = (1 + R_world_proxy_EUR) * (1 + world_fee_month) - 1
```

Core holdings and the unleveraged benchmark use `R_world_investable`. The raw public proxy remains a separate gross diagnostic. Official IWDA/SWDA NAV already contains fund expenses, so no fee is subtracted from the NAV side of validation. The leveraged sleeve uses only the separate 0.60% layer defined in section 14.1; neither World nor leveraged TER is charged twice.

The deterministic allocation backtest records:

- SWDA-equivalent 0.20% annual expense;
- leveraged ETF 0.60% annual expense where the ETF layer is modeled;
- Fineco commission of EUR 19 per executed sleeve order in the primary case;
- spread/slippage scenarios of 5, 10, and 20 basis points;
- monthly turnover by sleeve;
- funding cost separately from ETF expenses.

The Phase 2 historical comparison is pre-tax except for the corrected Rule E unit tests. A historical Italian average-cost tax ledger is not silently approximated. It will be integrated with Protocol G in the subsequent simulation phase.

## 17. Minimal component boundaries

Use the existing package and add only focused modules:

- `data_sources.py`: download, freeze, hash, and manifest raw sources;
- `point_in_time.py`: normalized observation contract and as-of selection;
- `market_proxy.py`: developed-world splice, FX conversion, and validation;
- `allocation.py`: signals and state machine;
- `backtest.py`: deterministic portfolio accounting and metrics.

The existing `policy.py`, `funding.py`, models, I/O, config, and tests receive the gross-fiscal migration. No generic provider framework, database, web service, optimizer, or plugin system is introduced.

## 18. Commands and outputs

The intended command flow is:

```text
python -m perpetual_engine data refresh --config config/data_sources_v1.json
python -m perpetual_engine backtest --config config/backtest_v1.json --output outputs/backtest_v1
```

`data refresh` is the only networked command. `backtest` is offline and deterministic.

Outputs include:

- raw and derived manifests with hashes;
- normalized point-in-time observations;
- monthly signal and allocation ledger;
- strategy equity curves;
- summary metrics and turnover/cost decomposition;
- World and leveraged-proxy validation reports;
- JSON run metadata sufficient to reproduce the run.

The same frozen inputs, configuration, and code must produce byte-identical normalized JSON.

## 19. Failure behavior

The engine fails closed or downgrades evidence explicitly when:

- a Damodaran URL is not the official domain;
- required workbook sheet or exact column is missing;
- the latest expected observation is stale;
- a source hash changes without a new manifest vintage;
- duplicate dates, internal gaps, nulls, or returns at or below -100% occur;
- an as-of join would use `available_at > decision_at`;
- FX direction or calendar alignment is ambiguous;
- validation thresholds fail;
- a requested daily conclusion relies only on the monthly proxy.

No missing series is silently forward-filled beyond a source-specific staleness limit.

## 20. Acceptance tests

### Gross Rule E

- both 3% and EUR 1,800 are gross targets;
- positive realized gain produces delivered net below the gross target;
- no gross-up occurs;
- gross outflow reconciles to net, tax, commission, and spread;
- the floor cap is never exceeded.

### Point-in-time

- annual 2007 ERP is unavailable before its 2008 `available_at`;
- the normalized monthly source begins exactly in September 2008, while a date-only September release first affects October under the conservative cut-off;
- a February 1 date-only annual fallback cannot affect February and first affects March;
- a month-end DGS10/DFII10 value with fallback availability in the next month is excluded from the prior-month mean at that decision;
- an IR3TIB month-M value using fallback availability first credits month M+2;
- every selected input satisfies `available_at <= decision_at`;
- month-t market outcomes cannot affect month-t decisions;
- changed source vintages change run identity rather than rewriting an old result.

### Damodaran parser

- official current URL is accepted;
- the stale root-level workbook, wrong sheets, wrong ERP columns, stale maximum date, nulls, and nonnumeric cells are rejected;
- `ERP (T12m)` is selected exactly.

### Market and FX

- monthly coverage from January 1979 has no unreported gaps or duplicates;
- EUR signal warm-up has complete proxy and synthetic FX coverage from January 1977 through December 1978;
- the July 1990 splice is explicit and stable;
- pre-1990 French archive members, columns, percent conversion, USA/World WDI ratio, two-year lag, and missing-value rule are exact;
- annual WDI weights are in [0,1], sum to one, and remain unchanged within each calendar year;
- an FX direction sentinel verifies the EUR-per-USD formula;
- a raw December 2023 value near 1.105 normalizes near 0.905 and a +10% USD asset return with a -10% currency factor produces -1% EUR;
- daily FX uses the last prior fixing, carries no more than seven calendar days, and has weekend/holiday sentinels;
- official USD NAV and proxy returns use the identical month-end FX factor before EUR validation;
- the November 2023 CPI link is continuous and fails if either splice observation is missing;
- monthly proxy validation uses the exact common sample and formulas and meets or fails pre-registered thresholds without moving them;
- absent official World or LWLD NAV produces the named failure state rather than a skipped validation.

### Leveraged proxy

- flat underlying plus positive funding yields a loss;
- a +10% then -9.0909% path demonstrates daily-reset behavior;
- a Friday-to-Monday interval accrues three calendar days;
- the DFF-to-LIBOR and LIBOR-to-SOFR transitions are exact and never bridged with the wrong source;
- FX is applied once;
- daily volatility drag is not double-counted;
- daily 0.60% TER accrues by actual calendar-day intervals, is absent from MSCI benchmark validation, and is present exactly once in LWLD validation;
- monthly residual drag and ETF TER appear as separate reconciled deductions;
- a monthly gross leveraged return at or below -100% wipes out the sleeve without a return below -100%;
- official 2014+ summary validation is reported.
- full daily, official-summary, and LWLD validation statuses use their pre-registered sample sizes and thresholds.

### Strategy and reproducibility

- beta thresholds, no-trade band, crisis compression, recovery ramp, and TIPS caps have boundary tests;
- no-trade compares proposed normal beta with prior accepted final beta; 0.149999 holds and 0.15 trades;
- Crisis, Recovery, TIPS, and no-trade precedence has conflict tests;
- simultaneous 90th/95th-percentile TIPS conditions choose the minimum 0.90 cap;
- Recovery ramps from `prior_accepted_final_beta`, while inception disables the no-trade band;
- the core is not traded tactically, drift is preserved, and binding core/no-borrowing caps are flagged;
- unchanged accepted beta and no-trade `HOLD` execute no overlay or defensive trades;
- inception and rebalance costs follow pretrade NAV -> gross targets -> own-sleeve costs -> returns; each comparator pays its specified initial/rebalance orders;
- all percentiles are expanding, not full-sample;
- percentile ties use the specified prior-only midrank;
- strategy weights sum to one;
- World 0.20% and leveraged 0.60% fee layers reconcile exactly and are not deducted from official NAV twice;
- same frozen inputs yield byte-identical outputs;
- the full existing and new test suite remains offline.

## 21. Independent review gate

Implementation is not complete until an agent who authored none of the implementation:

1. reads this specification, the implementation changes, manifests, and test evidence;
2. checks source-domain restrictions and point-in-time joins;
3. checks the gross-fiscal migration;
4. checks World and LWLD validation status;
5. issues a written `PASS` or `FAIL` report with file and line references.

A `FAIL` blocks completion until findings are fixed and re-reviewed, or explicitly presented to the user as unresolved.

## 22. Explicit non-goals

- claiming that the public developed-world proxy is MSCI World;
- claiming exact LWLD history before the product existed;
- introducing forward earnings yield without an audited history;
- full historical Italian tax-ledger simulation;
- Monte Carlo, bootstrap, walk-forward tuning, or PIR optimization;
- live portfolio trading or automatic broker instructions;
- changing the frozen distribution policy beyond the approved gross-fiscal correction.
