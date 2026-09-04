# Four-Sleeve + TCE Validation v1.0

Date frozen: 2026-08-27. This is research software, not trading authority.

## Objective

Validate, without ex-post weight optimization:

1. a structural `World / Momentum / Quality / Trend` portfolio over the latest reproducible 20-year common window;
2. a separately computed TCE selector, with optional notional up to 10%, over a retrospective 10-year window;
3. a prudent intramonth event rule in historical research and prospective shadow operation.

The existing E+G policy and Phase 2 backtest remain unchanged.

## Four-sleeve candidates

Weights are ordered `World / Momentum / Quality / Trend`:

- baseline: `60 / 15 / 15 / 10`;
- ERC Trend cap 15%: `28.09 / 22.64 / 34.27 / 15.00`;
- ERC Trend cap 20%: `26.53 / 21.03 / 32.44 / 20.00`;
- ERC unconstrained: `21.99 / 15.36 / 25.28 / 37.37`.

No other weight vector may be selected after seeing results. Primary benchmark is 100% World.
Fixed candidates rebalance at inception and each January; other months preserve drift.

## Frozen research proxies

- World: Kenneth French Developed Market total return, `Mkt-RF + RF`.
- Momentum: value-weighted developed large-cap/high-prior-return portfolio (`BIG HiPRIOR`) from the developed 2x3 size/momentum archive.
- Quality: value-weighted developed large-cap/robust-profitability portfolio (`BIG Robust`) from the developed 2x3 size/operating-profitability archive.
- Trend: AQR diversified monthly TSMOM factor plus the declared collateral return. It is a research proxy, not DBMF or an ETF backfill.

All results are converted consistently to EUR. ETF-level validation starts only at each product's actual inception.

The frozen four-sleeve window is exactly `2006-06-30` through `2026-05-31` (240 months). The May end is fixed before performance inspection because the official AQR workbook retrieved on 2026-08-27 ends in May 2026; it is not extended with a DBMF splice. The source-aware staleness ceiling is 120 days. Any error after the first month fails the whole build; it cannot silently truncate the series.

## TCE v1.0

Eight required scores in `[0,100]`:

```text
TCE = .15 Policy + .15 Capex/Orders + .20 Earnings/Revisions
    + .15 Relative Strength + .15 Valuation + .10 Crowding
    + .05 Macro + .05 Portfolio Fit
```

Missing input means `DATA_INCOMPLETE`; weights are never renormalized.

Vetoes:

- earnings below 40 and relative strength below 40: operational score capped at 64;
- valuation below 30 and crowding below 30: operational score capped at 69.

Eligibility requires operational TCE at least 70, confidence at least 60, and no veto. The thematic variant also requires two consecutive eligible month-end readings for a new entry. Two consecutive readings below 55 close an ordinary thematic position.

Historical research window is `2016-08-31` through `2026-07-31`, with warm-up from `2011-08-31`. It is labelled `RETROSPECTIVE_RESEARCH`, never out-of-sample. True out-of-sample evidence starts after the v1.0 configuration and code hashes are published.

## Fixed multi-asset universe

The frozen exposures are Nasdaq-100, World Value, World Small Cap, Defence, World Energy, Global Infrastructure, Gold, Broad Commodities, Euro Inflation-Linked Government Bonds, and EUR-hedged long US Treasuries. Historical tests use declared total-return exposure proxies; current ETF tickers are execution candidates only.

## Prudent intramonth rule

Only these event types can request a recalculation:

- enacted law or funded budget;
- awarded contract/order;
- earnings or guidance revision;
- regulatory approval/cancellation;
- programme cancellation;
- credit/default event.

At most one extraordinary rebalance is allowed per 30 days. It requires a thesis break, an incumbent score below 65, or a previously monthly-confirmed challenger with TCE at least 70 and a lead of at least five points. Execution uses the first available price after one complete trading session; same-event or same-close execution is forbidden. A news event cannot replace the second monthly confirmation for a new theme.

## Comparators and evidence

TCE sleeve comparators are equal-weight rebalanced, equal-weight buy-and-hold, cash when no candidate qualifies, and SWDA as an informational benchmark. The structural four-sleeve portfolio always sums to 100%. TCE deterministic results are computed and reported separately as an optional supplement of at most 10%; no automatic sale, pro-rata scaling or funding from the structural portfolio is assumed. Any combined view is an explicitly labelled scenario, not the base portfolio.

Reports must include CAGR, volatility, Sharpe, maximum drawdown, turnover, costs, chronological split, episode dependence, and placebo results. Multiple variants are not promoted by choosing the best historical result.
