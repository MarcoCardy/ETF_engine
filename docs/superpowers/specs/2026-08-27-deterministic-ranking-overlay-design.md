# Deterministic Ranking Overlay v1.0

Date frozen: 2026-08-28. Research software, not trading authority.

## Purpose and separation

The Deterministic Ranking Overlay (`DRO`) is a quantitative monthly selector. It is not the Thematic Confirmation Engine.

- `TCE-MA`: news, policy, orders, revisions, valuation, crowding and thesis confirmation.
- `DRO`: price-based ranking and trend filter only.

Their scores, decisions, positions and returns are stored and reported separately. Neither automatically changes or funds the 100% structural four-sleeve portfolio. Either may support a discretionary additional notional no greater than 10%, but combined use is not assumed.

## Frozen universe

The ten exposures are Nasdaq-100, World Value, World Small Cap, Defence, World Energy, Global Infrastructure, Gold, Broad Commodities, Euro Inflation-Linked Government Bonds and EUR-hedged long US Treasuries. SWDA/World is benchmark only.

The exposure list is fixed for all historical months. No theme may be added after observing its return.

## Two historical tracks

1. `CONDITIONAL_INDEX_PROXY`: declared total-return indices or exposure proxies, used to test the rule from January 2020 even where the current ETF did not yet exist.
2. `TRADABLE_ETF`: each current ETF becomes eligible only after its actual listing and sufficient warm-up. This is the only track that may be described as historically executable.

Proxy choice is based on exposure match, total-return availability and coverage before returns are scored. Proxy results cannot be presented as ETF returns.

## Frozen monthly signal

For candidate `i` at month-end `t`:

```text
ranking_return(i,t) = total_return_index(i,t-12) to total_return_index(i,t-1)
trend_ok(i,t)       = total_return_index(i,t) > mean(total_return_index(i,t-9:t))
absolute_ok(i,t)    = ranking_return(i,t) > 0
```

Candidates are ordered by descending `ranking_return`; ties are resolved by the frozen universe order. The selected candidate is the highest-ranked candidate satisfying both filters. If none qualifies, the position is cash.

All ranking levels and returns are expressed in EUR. Every USD source declares `DIVIDE_BY_USD_PER_EUR_DEXUSEU_AT_SAME_MONTH_END`; every EUR source declares `NONE_EUR_BASE`. FRED `DEXUSEU` is USD per EUR, so a USD level is divided by the USD-per-EUR fixing before any ranking or trend calculation. The monthly FX as-of is the last available observation at month-end, with source URL, inception and SHA-256 frozen in the configuration.

The skipped most recent month in the ranking is deliberate and frozen. It reduces short-term reversal sensitivity and prevents subsequent adjustment of lookback coefficients after seeing results.

There are no fitted weights, optimized thresholds, volatility scaling or discretionary overrides.

## Timing

- Warm-up begins in December 2018.
- First decision month: January 2020.
- Final complete historical decision month: July 2026.
- A separate `PARTIAL_AS_OF_2026-08-28` observation is calculated for August using only data available through its recorded retrieval timestamp. It reports the provisional selection and month-to-date return, but is excluded from CAGR, annualized volatility, Sharpe, full-month hit rates and model comparisons.
- Month-end signals use only observations available by that month-end.
- Returns begin with the next complete trading session; same-close execution is forbidden.
- Ordinary rotation occurs at most once per month.
- News does not alter DRO. Intramonth news remains exclusively within TCE-MA.

## Data integrity

- Adjusted total-return data are required; unadjusted prices cannot silently replace them.
- LBMA Gold PM is the sole no-income exception: it is a `PRICE_ONLY_NO_INCOME_ASSET`, not an adjusted-total-return series.
- Conditional sources are frozen as FRED `NASDAQXNDXNNR` (EQAC), official NAV for IWVL, WSML (EWSA), XDW0 (WENE), INFR, CMOD, IBCI and DTLE (DTEH), FRED `NASDAQNQUSB50201020N` for DFNS (US-only proxy caveat), and LBMA Gold PM for SGLD; SWDA official NAV is benchmark-only.
- Every source has observation date, retrieval time, inception date and content hash.
- Missing or stale data make that candidate ineligible for the month; remaining candidates are not rescored or renormalized.
- A failure after the first month fails the complete build; it cannot silently truncate the history.
- The complete universe and requested end date are validated before the monthly loop.
- Results are labelled retrospective and include the survivorship limitation created by freezing the 2026 universe.

## Costs and outputs

The primary normalized simulation applies ten basis points of spread/slippage to every entry and exit. Gross results are retained as a diagnostic. The fixed €19 commission is reported as a separate sensitivity at €5,000 and €80,000 notionals because a fixed fee cannot be represented honestly in a scale-free index simulation.

Cash earns the public euro overnight rate: €STR from its inception and the ECB's declared predecessor/back-cast for earlier required observations. Cash cannot be assigned a zero return merely for convenience.

Monthly output includes signal month, execution month, eligible candidates, ranking returns, trend flags, selected exposure, turnover, gross/net return, portfolio value and SWDA benchmark return.

The report includes CAGR, annualized volatility, Sharpe ratio, maximum drawdown, turnover, costs, calendar returns, monthly selections and comparison with SWDA, equal-weight monthly rebalancing and equal-weight buy-and-hold.

## Validation boundary

DRO performance validates only this mechanical ranking rule. It neither validates nor invalidates the news-based TCE. TCE-MA must be assessed through its prospective event and monthly-score ledger, plus any separately labelled timestamp-reconstructed event study.
