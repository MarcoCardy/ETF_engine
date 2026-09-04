# Perpetual Engine — Monthly Global Debasement Index Shadow Design

Date: 2026-08-21  
Status: proposed for final user review  
Depends on: `2026-08-21-perpetual-engine-historical-backtest-design.md`  
Does not supersede: any approved allocation, funding, tax, Crisis/Recovery, or Damodaran rule

## 1. Purpose

Add a reproducible Global Debasement Index (`GDI`) to the Perpetual Engine. The GDI is a monthly monetary-regime subsystem that measures:

1. market pressure against a diversified fiat numeraire through Gold/SDR;
2. broad-money growth in excess of currently knowable real growth;
3. financial repression through low real yields, confirmed by money and inflation.

The first release is **observational and validating only**. It must not change live portfolio weights. It produces shadow Value/Growth signals and tests whether those signals add information beyond the existing portfolio model.

The system also produces a weekly Gold/SDR Market Pulse and an arbitrary-day provisional nowcast. Neither can rewrite the official monthly history or silently become a trading rule.

## 2. Binding user decisions

- Calculate GDI officially once per calendar month.
- Keep the initial release observational and validating.
- Test whether GDI can favor a developed-world Value index over a developed-world Growth index.
- Test the prudent asymmetric rule first: confirmed high GDI may favor Value; all other states remain Neutral.
- Test the symmetric rule second: high GDI favors Value and low GDI favors Growth.
- Do not optimize weights, thresholds, lags, smoothing, holding periods, or satellite size.
- Use indices for the mathematical study. Select accumulating UCITS ETFs listed on Borsa Italiana only after the index model has been validated.
- Include GDI in the monthly report and workbook, and include the latest official GDI plus a Gold/SDR Market Pulse in the weekly report.
- Permit an arbitrary-day provisional calculation without treating it as an official month close.
- Use an independent review agent who did not author the specification or implementation.

## 3. Scope and non-authority

This component may:

- calculate and report monetary-regime scores;
- generate `INDICATIVE_ONLY` Value/Growth shadow states;
- compare fixed, pre-registered shadow portfolios with the existing model;
- provide data-quality, freshness, and evidence-grade diagnostics.

This component may not:

- change the approved Damodaran beta, Crisis/Recovery state, TIPS cap, leverage, core, defensive sleeve, Rule E, or Protocol G;
- place orders, create broker instructions, or alter the live workbook allocation fields;
- choose thresholds or products from realized performance;
- describe a public proxy as an MSCI index;
- convert a provisional nowcast into an official persistence month.

Any later promotion from shadow to live requires a separate user-approved design and implementation gate.

## 4. Evidence grades

Every GDI result carries one of these grades:

- `OFFICIAL_MONTHLY_FROZEN`: completed month, frozen source hashes, all inputs pass availability and staleness rules.
- `INTRAMONTH_PROVISIONAL`: arbitrary-day nowcast using only information available by the requested cut-off.
- `CURRENT_VINTAGE_RESEARCH`: historical value came from a source that may revise history and lacks a complete real-time vintage archive.
- `TRUE_VINTAGE_PIT`: historical value came from a frozen release vintage whose publication date is known.
- `LICENSED_MSCI_INDEX`: exact licensed MSCI World Value/Growth Net Total Return USD levels.
- `PUBLIC_STYLE_PROXY`: academic Fama/French developed-market style returns, not MSCI.
- `PUBLIC_MARKET_PROXY_DIAGNOSTIC`: intramonth Gold leg uses unadjusted `GC=F`, not spot Gold and never an official close.
- `DATA_INCOMPLETE`: an official monthly value cannot be produced.

Evidence grades are fields, not footnotes. A report must never silently mix exact-index and proxy results.

## 5. Time and point-in-time contract

### 5.1 Official monthly decision

For calendar month `t`:

```text
decision_at_t = final calendar instant of month t, 23:59:59 Europe/Rome
```

Store the equivalent UTC timestamp. The official `GDI_t` uses only rows satisfying:

```text
available_at <= decision_at_t
```

It is calculated after month-end from frozen files, but every selected row must have been available by the cut-off. `GDI_t` may first inform the shadow allocation applied to the return of month `t+1`.

A release date with no official intraday timestamp is normalized to 23:59:59 in the source's local timezone and then converted to UTC. It cannot affect an earlier same-day decision.

### 5.2 Required row metadata

Every raw or normalized row contains:

```text
series_id
observation_date
period_start
period_end
value
unit
available_at
source_url
retrieved_at
source_hash
parser_version
vintage_status
```

Joining by `observation_date` alone is forbidden. A changed source hash creates a new data vintage and run identity; it does not overwrite an earlier result.

### 5.3 Historical-vintage honesty

Release-vintage data are used when the official provider exposes them. When a provider exposes only revised current history, the frozen file is labelled `CURRENT_VINTAGE_RESEARCH`. Conservative availability lags prevent calendar look-ahead but cannot undo later statistical revisions. Reports distinguish these two limitations.

## 6. Percentile contract

All GDI component scores use expanding, within-series percentiles. They never use full-sample statistics.

For a current observation `x_t` and the `N_t` valid observations of the same normalized series through `t`:

```text
midrank_t = count(x_s < x_t) + 0.5 * count(x_s == x_t), for s <= t
Pct_t(x_t) = 100 * midrank_t / N_t
```

Properties:

- range `[0, 100]`;
- ties use the exact midrank above;
- the current observation is included;
- no winsorization, z-score clipping, or distribution fitting;
- a score is unavailable until the series has at least 36 valid monthly observations;
- missing months do not count toward the 36-observation minimum.

The calendar is frozen as follows:

```text
raw_start = 1996-01
percentile_history_start = 1997-01
first_reported_gdi_target = 2000-01
```

Raw observations before January 1996 are ignored. Normalized observations before January 1997 may be retained only as diagnostics and are excluded from every percentile rank. January 2000 therefore has exactly 37 candidate normalized calendar months, January 1997 through January 2000 inclusive, before missing-data exclusions. If any component has fewer than 36 valid normalized observations at January 2000, the engine reports the actual later start and `FAIL_TARGET_START_COVERAGE`; it does not fabricate history or expand the ranking window backward.

## 7. Source policy

Network access is permitted only in a refresh operation. Calculations and backtests are offline and deterministic.

Each refresh freezes the raw artifact, source URL, retrieval timestamp, HTTP metadata when present, SHA-256, parser version, first and last observation, unit, and redistribution restriction.

Official or primary institutional sources are preferred. Academic data are allowed only for clearly labelled public style proxies. Yahoo/yfinance is not an authoritative GDI macro source and never enters official monthly history; it is allowed only for the explicitly labelled Gold-futures Pulse/nowcast diagnostic and later ETF cross-checks.

## 8. Gold/SDR component — 40%

### 8.1 Official monthly source and units

The public official monthly GDI uses the World Bank Prospects Group Commodity Price Data, the Pink Sheet:

```text
landing_page = https://www.worldbank.org/en/research/commodity-markets
artifact_link_text = Monthly prices
artifact_filename = CMO-Historical-Data-Monthly.xlsx
sheet = Monthly Prices
date_column = first worksheet column (A), header cells blank
date_pattern = YYYYMmm
gold_column_header_row_5 = Gold
gold_column_unit_row_6 = ($/troy oz)
unit = nominal USD per troy ounce
frequency = monthly average
```

The current artifact URL is discovered from the official landing page and frozen with its SHA-256; the embedded document identifier may change with the vintage. The parser requires the exact sheet and identifies one and only one column whose row-5 cell is exactly `Gold` and row-6 cell is exactly `($/troy oz)`. It reads dates from the first worksheet column beginning at row 7 and requires `YYYYMmm`. The workbook has no contractual `Timestamps` field or `GOLD` code cell; `GOLD` is only our internal normalized series ID. Sentinel for the August-2026 artifact: cell `BR7`, corresponding to `1960M01`, equals `35`. A new artifact may change the physical column only if the two header cells and sentinel month/value validation still identify one unique series. The parser rejects the `Monthly Indices` sheet, precious-metals aggregate, annual data, and any field with a different unit.

The licensed high-frequency sensitivity may instead use LBMA Gold Price AM, USD per troy ounce, frozen locally under its license. It is never spliced into the public monthly official series and is not required for the official backtest.

SDR source:

- IMF Daily SDR Valuation and history: `https://www.imf.org/external/np/fin/data/rms_sdrv.aspx`
- IMF exchange-rate dataset landing page: `https://data.imf.org/Datasets/ER`

Normalize IMF data to:

```text
sdr_per_usd_d = SDR units for USD 1
```

Sentinel: a day on which IMF reports `USD 1 = SDR 0.735500` must normalize to `0.735500`, while `SDR 1 = USD 1.359620` must normalize to its reciprocal within source rounding.

Then:

```text
gold_sdr_d = gold_usd_per_oz_d * sdr_per_usd_d
```

The parser rejects the inverse formula.

### 8.2 Monthly alignment and observation

For the public official monthly series, calculate the arithmetic mean of IMF daily `sdr_per_usd_d` observations whose IMF date falls inside month `t`:

```text
SDRperUSD_t = mean(sdr_per_usd_d in month t)
GoldSDR_t = WorldBankGoldUSDMonthlyAverage_t * SDRperUSD_t
```

At least 15 valid IMF business-day observations are required. No carry from another month is allowed. The World Bank value for month `M` receives its actual Pink Sheet publication timestamp when archived; otherwise it is conservatively available at the final calendar instant of `M+1` in `America/New_York`. IMF daily data retain their actual date and become usable at 23:59:59 `America/New_York` when only a date is published. The Gold/SDR month is usable only at the later availability of the two inputs.

At decision month `t`, select:

```text
k(t) = latest GoldSDR observation month whose available_at <= decision_at_t
```

The signal is assigned to decision month `t` but retains `observation_month = k(t)`:

```text
gold_momentum_t = ln(GoldSDR_k(t) / GoldSDR_k(t)-12)
G_t = Pct_t(gold_momentum_t)
```

The two GoldSDR observations must be exactly 12 calendar months apart. The selected current observation may not be older than 75 days from its period end at `decision_at_t`; otherwise Gold is stale. This explicit release lag prevents a World Bank value published in `t+1` from entering the month-`t` decision.

The licensed LBMA sensitivity calculates the arithmetic mean of the final 20 same-day Gold/SDR observations in month `t`, using intersection without carry. It requires at least 15 common observations and is labelled `LICENSED_DAILY_20D_SENSITIVITY`; it never replaces or rewrites the public monthly score.

### 8.3 Live market source separation

The official GDI uses the World Bank monthly average for its whole history; it does not mix a 20-day endpoint statistic with an older monthly average. The weekly Pulse and arbitrary-day market update are separate diagnostics. Their preferred source is licensed LBMA Gold Price AM. If no licensed feed is configured, they use this exact fallback:

```text
provider = Yahoo Finance via yfinance
symbol = GC=F
field = unadjusted Close
auto_adjust = false
actions = false
repair = false
label = PUBLIC_GOLD_FUTURES_MARKET_PROXY
date_only_available_at = 23:59:59 America/New_York
```

Freeze the returned raw rows and retrieval metadata. Join this daily Gold proxy to IMF SDR by exact common calendar date without carry; both date-only timestamps are converted to UTC first. This vendor-defined front-futures history may include contract rolls. It can update the Pulse and the intramonth Gold leg of a provisional composite, which receives `PUBLIC_MARKET_PROXY_DIAGNOSTIC`; it cannot enter an official monthly GDI, cannot create an official persistence month or accepted shadow holding, and cannot be described as spot gold. Its provisional style state remains `INDICATIVE_ONLY`.

## 9. Global Excess Money component — 35%

### 9.1 Areas and official monetary series

The fixed area set is:

```text
USA, Euro area, China, Japan, United Kingdom
```

Primary monetary inputs:

- USA: Federal Reserve/FRED `M2SL`, monthly, seasonally adjusted, billions of USD. Source: `https://fred.stlouisfed.org/series/M2SL`.
- Euro area: ECB `BSI.M.U2.Y.V.M30.X.I.U2.2300.Z01.A`, M3 annual growth rate, monthly. Source: `https://data.ecb.europa.eu/data/datasets/BSI/BSI.M.U2.Y.V.M30.X.I.U2.2300.Z01.A`.
- China: PBOC monthly `Money & Quasi-money (M2)`, unit RMB 100 million. For each month, the refresh freezes the exact official PBOC release page and all candidate artifacts from `https://www.pbc.gov.cn/en/3688247/3688978/3709137/`. Source priority is uniquely `attached Money Supply table > attached Financial Statistics table > release-page HTML`. Within the highest available priority, select the latest official revision whose `available_at <= decision_at`; retain every earlier version and hash in the manifest. The parser accepts only a row whose normalized English label is exactly `Money & Quasi-money (M2)` or whose Chinese label is exactly `货币和准货币(M2)`, and whose unit is `100 million yuan`/`亿元`; it rejects a YoY percentage column, M1, M0, social financing, an annual-only table, or a release lacking the requested observation month. The raw manifest stores source priority, release-page URL, attachment URL or archived HTML, observation month, revision publication time, row label, unit, and hash. Historical months without a freezeable official artifact are `DATA_INCOMPLETE`, not filled from a commercial vendor.
- Japan: BOJ `MD02'MAM1YAM2M2MO`, M2 percent change from previous year in average amounts outstanding. Source: `https://www.stat-search.boj.or.jp/ssi/mtshtml/md02_m_1_en.html`.
- UK from July 2009: Bank of England `RPMB53Q`, monthly seasonally adjusted M4 excluding intermediate OFCs, sterling millions. Source family: `https://www.bankofengland.co.uk/boeapps/database/`.

### 9.2 UK historical splice

Monthly M4ex is not available before July 2009. To preserve the January 2000 GDI target without inventing monthly M4ex, construct `UK_BROAD_MONEY_SPLICE`:

- through June 2009: Bank of England full M4 `LPMAUYN`, monthly amounts outstanding, seasonally adjusted;
- from July 2009: M4ex `RPMB53Q`;
- link at July 2009:

```text
link = LPMAUYN[2009-07] / RPMB53Q[2009-07]
UK_level_t = LPMAUYN_t                         for t <= 2009-06
UK_level_t = RPMB53Q_t * link                  for t >= 2009-07
```

Both July 2009 observations are mandatory. The splice must have no level jump caused solely by units. It is labelled `UK_M4_TO_M4EX_SPLICE`; results from 2000–2009 also report a sensitivity using full M4 throughout.

Quarterly M4ex is not interpolated into monthly values.

### 9.3 Real GDP growth

Use the OECD Quarterly National Accounts dataset with this complete positional SDMX key:

```text
dataflow = OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,1.1
dimension_order = FREQ.ADJUSTMENT.REF_AREA.SECTOR.COUNTERPART_SECTOR.TRANSACTION.INSTR_ASSET.ACTIVITY.EXPENDITURE.UNIT_MEASURE.PRICE_BASE.TRANSFORMATION.TABLE_IDENTIFIER
key = Q.Y.USA+EA+CHN+JPN+GBR.S1.S1.B1GQ._Z._Z._Z.PC.L.GY.T0102
FREQ = Q
ADJUSTMENT = Y
REF_AREA = USA, EA, CHN, JPN, GBR
SECTOR = S1
COUNTERPART_SECTOR = S1
TRANSACTION = B1GQ
INSTR_ASSET = _Z
ACTIVITY = _Z
EXPENDITURE = _Z
UNIT_MEASURE = PC
PRICE_BASE = L
TRANSFORMATION = GY
TABLE_IDENTIFIER = T0102
accepted OBS_STATUS = A
```

Portal/API family:

`https://data-explorer.oecd.org/`  
`https://sdmx.oecd.org/public/rest/data/`

At a month-end decision, select the most recent quarterly real-GDP year-on-year growth observation satisfying the PIT contract. Do not linearly interpolate quarterly GDP and do not use a later revision as if it had been known earlier.

If an archived official release timestamp is absent, a quarter ending in month `Q` receives:

```text
available_at = final calendar instant of Q+2 months, Europe/Paris
```

This conservative fallback means the observation can first enter the month-end decision after that timestamp. A GDP observation older than 180 calendar days at decision time is stale.

### 9.4 Nominal GDP weights

Weights are annual and frozen within the calendar year. Use the latest official IMF WEO autumn vintage, September or October, published before January 1 of year `Y`, for GDP at current prices in billions of U.S. dollars for year `Y-1`. The 1999 vintage is therefore `WEO September 1999`, not a fabricated October release.

Areas are USA, Euro Area aggregate, China, Japan, and United Kingdom. The WEO archive and release date are part of the raw manifest:

- current dataset: `https://data.imf.org/Datasets/WEO`
- historical database archive: `https://www.imf.org/en/publications/sprolls/world-economic-outlook-databases`

```text
w_i,Y = NGDPD_i,Y-1 / sum_j(NGDPD_j,Y-1)
```

For a modern WEO bulk file, select `WEO Subject Code = NGDPD`, `Units = U.S. dollars`, `Scale = Billions`, and exact country/aggregate names `United States`, `Euro Area`, `China`, `Japan`, and `United Kingdom`. For a legacy file without subject codes, select the exact descriptor `Gross domestic product, current prices`, unit `U.S. dollars`, scale `Billions`, and the same five row names; the parser may use an explicit vintage-specific alias map stored in configuration, never a fuzzy match. The manifest freezes the official file, sheet/member, selected labels, and sentinel values. If the autumn vintage lacks any required row or year, use the latest official WEO spring or autumn vintage published before January 1, including April, September, or October, and record the substitution. Never use a later vintage for that year and never construct Euro Area by an undocumented country sum.

All five values are mandatory, nonnegative, and expressed in the same USD scale. Weights must sum to one within `1e-12` after normalization.

### 9.5 Money-excess calculation

At decision month `t`, for each area choose `m_i(t)`, the latest monetary observation satisfying the PIT and staleness rules, and `q_i(t)`, the latest quarterly GDP observation satisfying the same rules. For level series:

```text
money_yoy_i,t = 100 * (Money_i,m_i(t) / Money_i,m_i(t)-12 - 1)
```

For official year-on-year growth-rate series, use the published percentage directly. Scale checks reject decimals accidentally treated as percentages.

For each area:

```text
EM_i,t = money_yoy_i,t - real_gdp_yoy_i,q_i(t)
EMScore_i,t = Pct_t(EM_i,t)
M_t = sum_i(w_i,Y(t) * EMScore_i,t)
```

The month assigned to `EM_i,t` is the decision month; the ledger separately preserves monetary observation month `m_i(t)` and GDP quarter `q_i(t)`. `m_i(t)-12` means exactly 12 calendar months earlier, never the twelfth prior non-missing row.

Standardize each area before aggregation. Comparing raw M2, M3, and M4 levels across countries is forbidden.

### 9.6 Monetary availability and staleness

Use archived provider release timestamps when available. Otherwise an observation for month `M` has:

```text
USA        = final calendar instant of month M+1, America/New_York
Euro area  = final calendar instant of month M+1, Europe/Frankfurt
China      = final calendar instant of month M+1, Asia/Shanghai
Japan      = final calendar instant of month M+1, Asia/Tokyo
UK         = final calendar instant of month M+1, Europe/London
```

The fallback applies independently to USA, euro area, China, Japan, and UK. The current live refresh may use a verified earlier official release date, but the historical fallback is never moved earlier from realized knowledge of a modern calendar.

A money observation more than 75 calendar days past its period end at decision time is stale. Missing or stale data for any of the five areas causes `DATA_INCOMPLETE`; weights are not renormalized around a missing country.

## 10. Inflation breadth-intensity

Use OECD consumer-price data with these complete positional keys:

```text
dimension_order = REF_AREA.FREQ.METHODOLOGY.MEASURE.UNIT_MEASURE.EXPENDITURE.ADJUSTMENT.TRANSFORMATION
legacy_dataflow = OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0
legacy_key = USA+EA20+CHN+JPN+GBR.M.N.CPI.PA._T.N.GY
japan_new_dataflow = OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,1.0
japan_new_key = JPN.M.N.CPI.PA._T.N.GY
accepted OBS_STATUS = A
```

Use the legacy key for USA, EA20, China, and UK throughout. For Japan, use the legacy key through December 2018 and the COICOP-2018 key from January 2019. The two Japanese keys must match exactly over their January 2019–June 2021 overlap in the frozen current-vintage file; any mismatch above `1e-12` percentage points fails the splice. Sentinel: Japan January 2024 equals 2.2 percent in the current OECD vintage used to validate this contract. An OECD schema or data revision may change that sentinel only through a new frozen vintage and explicit spec review.

Source/API family:

`https://data-explorer.oecd.org/`  
`https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/`

At decision month `t`, choose `c_i(t)`, the latest CPI observation satisfying PIT and staleness. For each area:

```text
InflScore_i,t = Pct_t(cpi_yoy_i,c_i(t))
InflationBreadth_t = sum_i(w_i,Y(t) * InflScore_i,t)
```

The score is assigned to decision month `t`; the CPI observation month `c_i(t)` remains an auditable field.

This is intentionally a continuous breadth-and-intensity score, not a fitted inflation threshold. It uses the same annual nominal-GDP weights as the money component.

Use official release timestamps when available. Otherwise month `M` is available at the final calendar instant of `M+1` in `Europe/Paris`. An observation older than 75 calendar days is stale. Any missing area makes the official GDI incomplete.

## 11. Financial Repression component — 25%

### 11.1 Real-yield source

Use the Federal Reserve Bank of Cleveland 10-Year Real Interest Rate:

- series `REAINTRATREARAT10Y`;
- monthly, percent, not seasonally adjusted;
- source page: `https://www.clevelandfed.org/indicators-and-data/inflation-expectations`;
- FRED mirror: `https://fred.stlouisfed.org/series/REAINTRATREARAT10Y`.

The series begins in 1982 and is model-based. It is preferred over splicing a synthetic nominal-yield-minus-CPI series to post-2003 TIPS.

If a historical release timestamp is absent, observation month `M` receives:

```text
available_at = final calendar instant of M+1, US Eastern time
```

A value older than 60 calendar days at decision time is stale.

At decision month `t`, select:

```text
r(t) = latest real-yield observation month with available_at <= decision_at_t
       and age from period end <= 60 calendar days
```

Then `real_yield_10y_t` means the value observed in `r(t)`. Assign `L_t` and `Rstar_t` to decision month `t`, while preserving `real_yield_observation_month = r(t)` in the ledger. A month-`M` value receiving the fallback at the final instant of `M+1` cannot enter any earlier decision.

### 11.2 Confirmation-gated repression

Low real yields alone are not treated as debasement. Define:

```text
L_t = Pct_t(-real_yield_10y_t)
Gate_t = 0.50 * (M_t / 100) + 0.50 * (InflationBreadth_t / 100)
Rstar_t = L_t * Gate_t
```

`L_t` and `Rstar_t` lie in `[0,100]`; `Gate_t` lies in `[0,1]`.

Debt/GDP, fiscal deficits, central-bank balance sheets, and yield-curve-control events may appear as report diagnostics. They do not enter the v1 score.

## 12. Composite scores

The frozen formulas are:

```text
GDI_raw_t = 0.40 * G_t + 0.35 * M_t + 0.25 * Rstar_t
Pressure_raw_t = 0.60 * G_t + 0.40 * L_t
Confirmed_raw_t = 0.60 * M_t + 0.40 * Rstar_t
```

Each lies in `[0,100]`.

For each score independently, calculate a three-month exponential moving average:

```text
alpha = 2 / (3 + 1) = 0.5
EWMA_t = 0.5 * raw_t + 0.5 * EWMA_t-1
EWMA_first = raw_first
```

No missing month is skipped inside the EWMA. A missing official GDI breaks official persistence; when the series resumes, EWMA restarts from that month's raw value and the persistence counter resets to zero.

## 13. Regime labels

The official GDI EWMA label is:

```text
[0,20)    HARD_MONEY_DISINFLATION
[20,40)   LOW_DEBASEMENT
[40,55)   NORMAL
[55,70)   SIGNIFICANT_DEBASEMENT
[70,85)   STRONG_DEBASEMENT
[85,100]  EXTREME_MONETARY_REGIME
```

Exact boundary values enter the higher regime except 100, which remains in the final closed interval.

Reports show raw and EWMA values. Shadow rules use EWMA only.

## 14. Shadow signal rules

### 14.1 Persistence

A condition is confirmed only after it is true in two consecutive complete official months. Provisional nowcasts never increment or reset the official counter.

### 14.2 Primary prudent rule

```text
high_confirmed_t = (GDI_EWMA_t >= 55) and (Confirmed_EWMA_t >= 55)
```

If `high_confirmed` is true for two consecutive official months:

```text
PRUDENT_STATE = VALUE_FAVORED
```

Otherwise:

```text
PRUDENT_STATE = NEUTRAL
```

If `Pressure_EWMA >= 55` while `Confirmed_EWMA < 55`, also report:

```text
diagnostic = PRESSURE_ONLY
```

`PRESSURE_ONLY` remains Neutral and cannot favor Value.

### 14.3 Secondary symmetric rule

Value side:

```text
same high_confirmed rule and two-month persistence -> VALUE_FAVORED
```

Growth side:

```text
low_confirmed_t = (GDI_EWMA_t <= 40) and (Confirmed_EWMA_t <= 40)
```

If `low_confirmed` is true for two consecutive official months:

```text
SYMMETRIC_STATE = GROWTH_FAVORED
```

Otherwise the symmetric state is Neutral. A month cannot satisfy both sides. Equality at 55 enters the Value condition; equality at 40 enters the Growth condition.

### 14.4 Inception and missing data

The first valid month has a persistence count of one when its condition is true. No signal is confirmed until the next consecutive official month.

The GDI shadow comparison begins at the first business day of January 2000, before the January official GDI exists. Its initial state is `NEUTRAL`, and its style slot already owns the same broad developed-World exposure as the approved baseline. This is a relabelling of existing World notional, not a new purchase, so GDI inception cost is zero. The January 2000 month-end signal can first affect the slot at the first business day of February 2000.

`DATA_INCOMPLETE` resets both persistence counters and the EWMA as specified above, but it does not force a trade caused solely by missing data. If the slot already exists, retain its last accepted holding and earn that holding's normal return under state `HOLD_DATA_INCOMPLETE`; no GDI order or cost is booked. At inception, incomplete data leave the slot in the initial broad-World holding. The first later complete month restarts EWMA and persistence from one and cannot schedule a favored state until a second consecutive complete month confirms it.

## 15. Value/Growth target indices and public proxy

### 15.1 Conceptual target

The frozen target pair is:

- MSCI World Value Index, Net Total Return, USD;
- MSCI World Growth Index, Net Total Return, USD.

Official pages:

- `https://www.msci.com/indexes/index/105867/msci-world-growth-index`
- `https://www.msci.com/indexes/group/value-and-growth-indexes`
- MSCI index data search: `https://app2.msci.com/products/index-data-search/`

The study must select the same developed-world universe, net-return variant, and USD base for both styles. A gross Value series may not be compared with a net Growth series.

MSCI data are subject to MSCI terms and may require a license. Exact levels are used only when lawfully available and frozen with `LICENSED_MSCI_INDEX` grade.

### 15.2 Public long-history proxy

When exact MSCI levels are unavailable, use Kenneth French's official academic dataset:

```text
file = Developed_6_Portfolios_ME_BE-ME_CSV.zip
member = Developed_6_Portfolios_ME_BE-ME.csv
table = Average Value Weighted Returns -- Monthly
growth column = BIG LoBM
value column = BIG HiBM
start = July 1990
```

Source and methodology:

- `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html`
- `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_6_Portfolios_ME_BE-ME_CSV.zip`
- `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/six_portfolios_developed.html`

The exact file URL is frozen from the data-library link at refresh time; redirects and member names are validated.

The big-stock portfolios are selected because French defines developed-market big stocks as the top 90% of June market capitalization, closer to MSCI World large/mid cap than a mechanically equal-weighted big/small blend. Returns are percent, USD, value weighted, and include dividends and capital gains; divide by 100 once.

Labels are:

```text
PUBLIC_DEVELOPED_BIG_VALUE_TR_PROXY_USD
PUBLIC_DEVELOPED_BIG_GROWTH_TR_PROXY_USD
```

They are book-to-market proxies, not the multi-variable MSCI Value/Growth methodology.

### 15.3 EUR conversion

Apply the same month-end `EUR_per_USD` factor and calendar contract already frozen in the Phase 2 historical-backtest design:

```text
1 + R_style_EUR,t = (1 + R_style_USD,t) * (FX_EURperUSD_t / FX_EURperUSD_t-1)
```

FX is applied exactly once to Value and Growth. Both series use the same fixing dates. Do not hedge and do not double the FX return.

### 15.4 Exact-index/proxy validation

If exact MSCI monthly Net USD returns are available, compare each public proxy with its matching MSCI style over the longest uninterrupted common segment. If multiple segments have the same maximum month count, choose the one with the latest end month; if still tied, choose the latest start month. Define:

```text
corr = Pearson monthly-return correlation
beta = OLS slope with intercept: MSCI_return = alpha + beta * proxy_return
tracking_error = sample_std(proxy - MSCI, ddof=1) * sqrt(12)
CAGR = product(1+r)^(12/n) - 1
```

Pre-registered descriptive thresholds for a `PROXY_VALIDATED` label are:

- at least 60 common monthly observations;
- correlation at least 0.90;
- beta from 0.75 to 1.25;
- annualized tracking error no more than 8%;
- absolute CAGR gap no more than 4 percentage points.

Failure does not erase the public-proxy study. It changes the label to `PROXY_NOT_EQUIVALENT` and blocks exact-MSCI claims.

## 16. Forward validation target

For every official month `t`, calculate future style spreads without allowing those outcomes into the signal:

```text
Spread_h,t = compounded Value EUR return from t+1 through t+h
             - compounded Growth EUR return from t+1 through t+h
```

Primary horizon:

```text
h = 12 months
```

Secondary horizons are 3 and 6 months. One month is diagnostic only.

A `valid signal month` is a complete official GDI month with a non-suppressed prudent or symmetric signal, as applicable, and a complete forward outcome for the requested horizon. `DATA_INCOMPLETE` and `HOLD_DATA_INCOMPLETE` months are excluded from the unconditional sample, state samples, split, regression, and cohorts. Incomplete terminal horizons are dropped before every statistic, split, cohort, or regression. For the prudent rule, compare `Spread_12m` in `VALUE_FAVORED` months with Neutral months and the unconditional valid-signal sample. Report:

- sample size;
- arithmetic mean;
- median;
- hit rate `P(Spread_12m > 0)`;
- difference from unconditional mean and median;
- first-half and second-half results using a chronological split fixed at the midpoint of valid signal months;
- largest episode contribution and leave-one-episode-out results.

Sort valid signal months chronologically. For a state with `N` valid observations, the first half contains the first `floor(N/2)` observations and the second half contains the remaining `ceil(N/2)`. Promotion is `INSUFFICIENT_SAMPLE` unless each half contains at least 12 observations of the state. An episode is a contiguous run of the same confirmed state in the original complete monthly signal ledger; dropping terminal outcomes does not merge episodes.

For episode `e`, define:

```text
episode_count_share_e = n_e / N_state
episode_abs_spread_share_e = abs(sum_{t in e} Spread_12m,t)
                             / sum_{t in state} abs(Spread_12m,t)
```

If the denominator is zero, all absolute-spread shares are zero. `largest episode contribution` is the maximum of both shares, with the episode identifier reported. Leave-one-episode-out removes one full episode at a time and recomputes state mean, median, hit rate, and sample size. “Sign not dependent on one episode” means that after removing every episode individually, both mean and median retain the required sign. A state with fewer than two episodes fails this criterion.

Overlapping monthly forward returns are descriptive, not independent observations. The inferential regression is fixed as:

```text
Spread_12m,t = alpha + beta * I(state_t = favored) + error_t
```

Estimate OLS with an intercept on all valid favored and Neutral months and report `beta` with Newey-West/HAC covariance, Bartlett kernel, lag 11, and no small-sample correction. Inference uses:

- Newey-West/HAC standard errors with lag 11 for the 12-month horizon;
- twelve staggered non-overlapping annual cohorts, identified by start-month modulo 12.

For cohort `c in {1,...,12}`, include valid signal months whose forward outcome starts in calendar month `c`. For each cohort and each reported state, freeze and report `N`, arithmetic mean, median, and the state-appropriate hit rate. A cohort with `N=0` is reported as empty, not dropped; no promotion criterion is added or optimized from cohort results.

Promotion evidence for the prudent Value state requires all of:

- mean 12-month spread positive;
- median 12-month spread positive;
- hit rate above 50% in both chronological halves;
- the leave-one-episode-out sign test above passes for both mean and median;
- no data-integrity or PIT failure.

This is a pre-registered validation criterion, not an invitation to move thresholds. Failure means the GDI remains observational.

After the prudent test is frozen, run the same tables and minimum-sample rules for the symmetric rule. Its Growth state is successful when the corresponding Value-minus-Growth mean and median are negative, its Growth hit rate `P(Spread_12m < 0)` exceeds 50% in both halves, and every leave-one-Growth-episode-out mean and median remains negative.

## 17. Fixed shadow portfolio comparison

The factor satellite is fixed before results are observed:

- primary size: 10% of portfolio NAV;
- sensitivity sizes: 5% and 15%;
- no ex-post choice among the three.

The remaining architecture is unchanged: total unleveraged equity exposure, leveraged sleeve, defensive sleeve, Damodaran beta, Crisis/Recovery, TIPS rules, Rule E, and Protocol G follow the approved engine.

This is a research decomposition, not a live sale of protected core units. At shadow inception, the approved unleveraged World sleeve is split into:

```text
unchanged broad-World remainder
style slot = 10% of total portfolio NAV
```

The style slot replaces the same notional amount of broad World, so total portfolio weight and accounting beta do not increase. Sensitivity runs replace 5% or 15% instead. If the available unleveraged World target is smaller than the requested slot, return `FAIL_STYLE_SLOT_EXCEEDS_WORLD`.

Comparators:

1. previous approved model without GDI;
2. prudent GDI shadow with 10% satellite;
3. symmetric GDI shadow with 10% satellite;
4. pre-declared 5% and 15% sensitivities.

In a favored state, the satellite holds the corresponding style index. In Neutral, it holds the same broad developed-world investable return used by the previous model; Neutral does not hold cash and does not create a hidden tactical de-risking rule.

At the end of month `t`, a newly confirmed state schedules a style-slot switch at the start of `t+1`. The slot trades only when its accepted holding changes. It switches its entire pre-trade slot value without topping up to 10%, so subsequent drift is preserved. A switch pays one sale and one purchase order, using the same Phase 2 commission and pre-registered spread/slippage scenario; costs are charged to the slot before the `t+1` return. Inception is the zero-cost relabelling defined in §14.4. If the holding is unchanged or state is `HOLD_DATA_INCOMPLETE`, GDI causes no trade. Index returns contain no ETF TER; an ETF expense layer is added only after products are selected and validated.

All comparators use identical rebalance dates, cost assumptions, FX, and portfolio cash-flow timing. The GDI comparison may not alter another engine parameter.

## 18. Arbitrary-day nowcast

`GDI_INTRAMONTH_NOWCAST(as_of)` is available for any requested timestamp.

For any provisional normalized value `x` and the frozen official history `H` through the prior complete month, use the virtual-append percentile:

```text
N = |H|
Pct_OOS(x | H) = 100 * (count_H(value < x) + 0.5 * (count_H(value == x) + 1)) / (N + 1)
```

This is exactly the §6 current-inclusive midrank that would result if the provisional value were appended once, but neither `x` nor its rank is stored in official history. Equality uses exact normalized numeric equality after source-unit conversion.

It:

- uses only rows with `available_at <= as_of`;
- updates Gold/SDR through the latest common completed source day;
- uses the latest newly released money, GDP, CPI, and real-yield values available by `as_of`;
- maps provisional observations with `Pct_OOS` against the frozen official expanding history through the prior official month;
- does not append the provisional value to that history;
- reports the age and observation date of every carried macro input;
- never increments official persistence;
- never overwrites `GDI_OFFICIAL_MONTHLY`.

Output labels:

```text
status = PROVISIONAL
portfolio_authority = INDICATIVE_ONLY
message = "Value/Growth state if the month closed at as_of"
```

If all components are valid, output a complete nowcast. If at least Gold/SDR is valid but a macro component is missing or stale, output `PARTIAL_NOWCAST`, list the unavailable component, and suppress the composite GDI and provisional style state.

## 19. Weekly Gold/SDR Market Pulse

The weekly report does not recalculate an official weekly GDI. It shows the latest official monthly GDI and a separate diagnostic pulse.

For report cut-off `w`:

```text
PulseLevel_w = arithmetic mean of the final 5 common valid Gold/SDR daily observations <= w
prior_cutoff_w = w - 365 calendar days
PriorPulseLevel_w = arithmetic mean of the final 5 common valid observations <= prior_cutoff_w
PulseMomentum_w = ln(PulseLevel_w / PriorPulseLevel_w)
```

No future-date nearest match is allowed. Both windows require exactly five common observations, each spanning no more than ten calendar days; otherwise the Pulse is stale.

Map `PulseMomentum_w` with the same `Pct_OOS` formula to the frozen official monthly gold-momentum distribution available through the latest complete official month:

```text
PulsePct_w = Pct_OOS(PulseMomentum_w | official monthly gold_momentum history)
```

The weekly observation is never appended as another percentile-history row. This prevents four correlated weekly observations from masquerading as four independent months.

Pulse labels:

```text
[0,40)    LOW
[40,55)   NORMAL
[55,85)   HIGH
[85,100]  EXTREME
```

The canonical weekly cutoff is Friday 23:59:59 `Europe/Rome`. A scheduled report uses that cutoff even if Friday is a market holiday; its five observations remain nearest-prior observations under the ten-day rule. `change in percentile` is always relative to the immediately preceding canonical Friday cutoff, recomputed from frozen source data, never relative to the last arbitrary execution. An ad-hoc Pulse at another timestamp reports its level and percentile but leaves `change_from_prior_week` null.

The pulse reports:

- five-day Gold/SDR level;
- 12-month log return and simple percentage equivalent;
- percentile and label;
- change in percentile from the prior canonical Friday cutoff;
- gold and SDR last source dates;
- staleness and source-vintage flags.

It cannot change monthly GDI, persistence, or a shadow portfolio state.

## 20. Report and workbook contract

### 20.1 Monthly report

The monthly report contains:

- GDI raw and EWMA;
- Pressure raw and EWMA;
- Confirmed raw and EWMA;
- `G`, `M`, `L`, `InflationBreadth`, `Gate`, and `Rstar`;
- regime label and two-month persistence state;
- prudent and symmetric `INDICATIVE_ONLY` states;
- Value/Growth validation tables and evidence grade;
- data freshness, vintage status, source dates, hashes, and failure flags;
- optional arbitrary-day nowcast clearly separated from the official close.

### 20.2 Weekly report

The weekly report contains:

- latest frozen official monthly GDI and regime;
- age of each official component;
- Gold/SDR Market Pulse;
- optional arbitrary-day nowcast;
- no fabricated weekly regime transition.

### 20.3 Workbook sheets

The future standalone workbook contains exactly these GDI sheets:

1. `GDI Dashboard`
2. `GDI History`
3. `Value Growth Validation`
4. `Data Vintages`

`GDI Dashboard` is readable without formulas hidden in presentation cells. `GDI History` is one row per official month. `Value Growth Validation` contains frozen rules, index/proxy grades, forward outcomes, cohorts, and episode diagnostics. `Data Vintages` contains the audit fields and hashes.

The workbook consumes exported deterministic tables. It is not the calculation engine and does not download data.

## 21. Minimal component boundaries

Reuse the Phase 2 data, PIT, FX, backtest, reporting, and manifest infrastructure. Add only focused GDI logic:

- GDI source definitions in the existing source configuration;
- `gdi.py` for normalized component formulas, percentiles, EWMA, and signal states;
- GDI validation functions beside the existing deterministic backtest metrics;
- report/workbook exports through existing output boundaries.

Do not add a database, web service, optimizer, provider plugin framework, scheduler, or broker integration.

## 22. Intended commands and outputs

The existing refresh remains the only networked operation. Intended offline commands are:

```text
python -m perpetual_engine gdi monthly --as-of 2026-07-31 --output outputs/gdi_2026-07
python -m perpetual_engine gdi nowcast --as-of 2026-08-21T12:00:00+02:00 --output outputs/gdi_nowcast_2026-08-21
python -m perpetual_engine gdi validate --output outputs/gdi_validation_v1
```

Outputs include:

- normalized component ledger;
- official monthly GDI ledger;
- provisional nowcast JSON when requested;
- weekly Gold/SDR Pulse row;
- Value/Growth index or proxy ledger;
- validation metrics, cohorts, and episodes;
- shadow portfolio comparison;
- source and run manifests with hashes;
- flat CSV/JSON tables for the future report and workbook.

Same frozen inputs, configuration, and code must produce byte-identical normalized JSON and CSV ordering.

## 23. Failure behavior

Official monthly GDI is `DATA_INCOMPLETE` and the style state is suppressed when:

- any of the five required money, GDP, or CPI areas is missing or stale;
- Gold or SDR coverage is insufficient or quote direction is ambiguous;
- the real-yield series is missing or stale;
- an input violates `available_at <= decision_at`;
- a percentile has fewer than 36 valid observations;
- weights are missing, negative, non-finite, or do not normalize;
- duplicate dates, null required values, impossible units, or non-finite results occur;
- source hashes change without a new vintage identity.

The engine downgrades evidence rather than aborting unrelated outputs when:

- MSCI exact levels are unavailable but the public proxy is valid;
- public proxy validation misses a pre-registered threshold;
- the weekly Pulse is stale while the latest official monthly GDI remains valid.

No missing area is silently dropped and no weights are silently renormalized.

## 24. Acceptance tests

### PIT and reproducibility

- every selected input satisfies `available_at <= decision_at`;
- a month-M date-only macro fallback cannot enter a decision before the end of M+1;
- month-t GDI first applies to the month-t+1 shadow outcome;
- a provisional nowcast never alters official history or persistence;
- source revision changes run identity rather than rewriting an old result;
- frozen inputs produce byte-identical outputs.

### Percentiles and smoothing

- 36 observations are required;
- raw history begins exactly 1996-01 and percentile ranking begins exactly 1997-01, with older source observations excluded;
- January 2000 has 37 candidate normalized ranking months before missing-data exclusions;
- ties use the exact current-inclusive midrank;
- provisional and Pulse ranks use the exact virtual-append `Pct_OOS` formula and never alter official history;
- no full-sample percentile is used;
- EWMA uses alpha 0.5 and the stated seed;
- missing official months reset EWMA and persistence;
- all component and composite scores remain in `[0,100]`.

### Gold/SDR

- `gold_usd * sdr_per_usd` is used, never its inverse;
- the IMF reciprocal sentinel passes;
- the World Bank parser selects `Monthly Prices`, the unique row-5 `Gold`/row-6 `($/troy oz)` column and first-column `YYYYMmm` dates, never `Monthly Indices`;
- the public monthly calculation uses the in-month IMF SDR mean with at least 15 observations and no cross-month carry;
- the licensed 20-day sensitivity uses same-day intersection without carry and cannot overwrite the public official score;
- Yahoo `GC=F` is labelled as a futures diagnostic and cannot enter official GDI or persistence;
- weekly observations do not enter monthly percentile history;
- a weekly nearest-prior 12-month window never uses a future date.
- scheduled weekly change is relative to the prior canonical Friday, while an ad-hoc Pulse has null weekly change.

### Money and inflation

- M2/M3/M4 are standardized within area before aggregation;
- published percent and decimal scale sentinels reject a 100x error;
- latest available quarterly GDP is selected by availability, not observation date;
- OECD GDP parser requires the complete frozen positional key and rejects any differing dimension;
- OECD CPI parser requires the complete frozen key and the Japan January-2019 splice;
- no monthly GDP interpolation occurs;
- IMF WEO weights accept the official September/October autumn vintage and use only a vintage published before the calendar year;
- all five weights are present and sum to one;
- the July 2009 UK splice uses both required levels and records its label;
- missing country data fails instead of renormalizing;
- competing PBOC artifacts follow the exact source-priority and latest-eligible-revision rule;
- staleness boundaries at 60, 75, and 180 days have exact tests.

### Repression and composite

- low real yield with low money and inflation confirmation produces low `Rstar`;
- a real-yield observation for month M with fallback availability at end M+1 cannot enter an earlier decision, and its observation month remains auditable;
- `Gate`, `Rstar`, GDI, Pressure, and Confirmed reconcile exactly to their formulas;
- debt/GDP changes no v1 score;
- boundary values 20, 40, 55, 70, and 85 receive the specified regime.

### Shadow rules

- one high month is Neutral and two consecutive high months favor Value;
- `Pressure-only` never favors Value;
- the prudent rule never favors Growth;
- two consecutive months with both GDI and Confirmed exactly 40 favor Growth only in the symmetric test;
- exactly 55 enters the high condition;
- missing data resets persistence;
- shadow inception is January 2000 Neutral with existing World notional and zero GDI inception cost;
- an incomplete month after a favored state holds the last accepted instrument, books no GDI trade, and resets EWMA/persistence;
- provisional states never count as a persistence month.

### Value/Growth validation

- exact MSCI and public proxy grades cannot be confused;
- Value and Growth use the same return variant and FX dates;
- Fama/French parser selects only the monthly value-weighted table and exact `BIG LoBM`/`BIG HiBM` fields;
- returns are divided by 100 exactly once;
- the style slot replaces, rather than adds to, the same broad-World notional and cannot exceed the available World sleeve;
- a state change at month-end pays exactly one sale and one purchase before the next month's return, while an unchanged state pays no GDI trading cost;
- the style slot is not topped up after inception and its drift is preserved;
- forward 12-month outcomes begin at t+1;
- incomplete forward horizons are dropped before all statistics;
- `DATA_INCOMPLETE` and `HOLD_DATA_INCOMPLETE` are absent from unconditional, state, regression, and cohort samples;
- chronological halves use floor/ceil and require at least 12 favored-state observations per half;
- exact-index/proxy comparison selects the longest common segment with the stated latest-end/latest-start tie-break;
- HAC regression, lag 11, Bartlett kernel, and twelve cohorts are deterministic, including empty cohorts and per-state N/mean/median/hit rate;
- contiguous episodes, count/absolute-spread contribution, and every leave-one-episode-out sign test are reproducible;
- proxy validation metrics use the exact formulas and fixed thresholds;
- 5%, 10%, and 15% satellite runs are all reported without selecting a winner.

### Reporting

- monthly and weekly reports distinguish official, provisional, proxy, and incomplete states;
- the weekly report shows the latest official monthly GDI rather than a fake weekly GDI;
- workbook tables reconcile to JSON/CSV exports;
- every displayed score has observation date, availability/freshness, and run vintage available in `Data Vintages`.

## 25. Independent review gate

The specification and later implementation are not approved until an agent who authored neither:

1. reads this specification and its dependency;
2. checks exact series, units, source domains, and stale-source behavior;
3. checks every PIT join and t-to-t+1 alignment;
4. checks percentile, EWMA, persistence, and Value/Growth formulas;
5. checks that the GDI remains non-authoritative;
6. checks report/workbook auditability;
7. issues a written `PASS` or `FAIL` with file and line references.

A `FAIL` blocks implementation planning until findings are fixed and re-reviewed, or explicitly presented to the user as unresolved.

## 26. Explicit non-goals

- optimizing the GDI or style rule;
- claiming causality from a descriptive regime indicator;
- using the nowcast as an official close;
- selecting ETFs before the index study is frozen;
- backfilling ETF NAV before product inception;
- changing the approved portfolio engine;
- live trading or broker connectivity;
- adding fiscal debt variables to the v1 score;
- using weekly observations as statistically independent monthly evidence.
