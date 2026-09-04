# Structural Portfolio + TCE — validation report v1

## Status

- Four-sleeve historical test: **completed**.
- `THREE_EQUITY_FIXED`: **current implementation candidate**, using only ETFs listed in Milan and preserving the previously frozen equity proportions.
- `PRUDENT_TREND_10`: **implemented as a separate post-analysis sensitivity candidate**; the previously frozen `ERC_TREND_15` comparator is unchanged.
- TCE rules engine, monthly transitions and material-news transitions: **implemented and tested**.
- Ten-year TCE performance backtest: **not validated**, because the eight historical component scores are not available as point-in-time observations and their numeric mappings were never frozen.

The four-sleeve result is retrospective research based on the current versions of the source archives. It is not presented as a point-in-time signal history.

## Frozen four-sleeve test

- Window: 2006-06-30 through 2026-05-31, exactly 240 months.
- Currency: EUR, unhedged. USD total returns are converted with the final monthly DEXUSEU fixing.
- Rebalance: inception and January only; weights drift during the year.
- Initial capital: EUR 800,000.
- Cost per executed sleeve order: EUR 19 plus 10 bps spread/slippage.
- The original frozen candidates were not fitted or changed after inspecting returns. `THREE_EQUITY_FIXED` simply normalizes the existing equity proportions to 100%; it does not run a new optimization. Trend variants remain research comparators.
- Trend total return: AQR diversified TSMOM excess return plus the Kenneth French Developed monthly risk-free return, added exactly once.

Sources:

- World and risk-free: Kenneth French Developed 3 Factors.
- Momentum: Kenneth French Developed, value-weighted BIG HiPRIOR.
- Quality: Kenneth French Developed, value-weighted BIG HiOP (reported as BIG Robust).
- Trend: AQR Time Series Momentum aggregate factor.
- FX: Federal Reserve/FRED DEXUSEU.

## Results after costs

| Portfolio | Weights: World / Momentum / Quality / Trend | CAGR | Volatility | Maximum drawdown | Ending NAV |
|---|---:|---:|---:|---:|---:|
| World only | 100 / 0 / 0 / 0 | 9.41% | 13.39% | -48.21% | EUR 4.834m |
| Baseline | 60 / 15 / 15 / 10 | 10.00% | 11.77% | -38.35% | EUR 5.382m |
| **Three Equity Fixed** | **33.0471 / 26.6353 / 40.3176 / 0** | **10.49%** | 13.15% | -43.83% | EUR 5.883m |
| Prudent Trend 10 | 29.7424 / 23.9718 / 36.2859 / 10 | **10.50%** | 11.79% | -36.08% | **EUR 5.890m** |
| ERC Trend 15 | 28.09 / 22.64 / 34.27 / 15 | 10.47% | 11.25% | -32.14% | EUR 5.859m |
| ERC Trend 20 | 26.53 / 21.03 / 32.44 / 20 | 10.42% | 10.83% | -28.34% | EUR 5.806m |
| ERC free | 21.99 / 15.36 / 25.28 / 37.37 | 10.09% | **10.28%** | **-15.88%** | EUR 5.467m |

### Robustness checks

Over the last ten years of the sample (2016-06 through 2026-05), World returned 12.35% annualized. Three Equity Fixed returned 13.60%, with volatility of 13.48% and maximum drawdown of -17.87% instead of -20.45%. Prudent Trend 10 returned 12.76%, with volatility of 11.86% and maximum drawdown of -14.90%.

Across all available rolling ten-year windows, the frozen candidates beat World with these frequencies:

| Candidate | Win rate vs World | Median annualized excess | Worst annualized excess |
|---|---:|---:|---:|
| Baseline | 71.1% | +0.29% | -0.89% |
| ERC Trend 15 | **80.2%** | **+0.68%** | -1.31% |
| ERC Trend 20 | 66.1% | +0.47% | -1.82% |
| ERC free | 37.2% | -0.33% | -3.61% |

The full-sample advantage of the Trend variants is mainly downside protection. Removing Trend leaves the 20-year CAGR almost unchanged versus Prudent Trend 10 (10.49% versus 10.50%), but increases volatility and drawdown. `THREE_EQUITY_FIXED` is nevertheless the implementation candidate because all three instruments are listed in Milan and the Trend ETF is relatively expensive, listed in Paris and lacks long live history. This is an execution decision, not a claim that Trend diversification has no value.

## TCE and the retrospective-reconstruction problem

The original TCE documents freeze weights and thresholds, but they do not define a deterministic historical mapping from raw observations to the 0–100 component scores. Five blocks are especially affected: Policy, Capex/Orders, Earnings revisions, Valuation and Crowding. Historical ETF prices alone cannot reconstruct them. Selecting past themes after learning which themes became important would add a second, larger look-ahead bias.

Therefore a ten-year TCE return series has not been manufactured. The implementation rejects a month when any required component or its evidence is missing; it never renormalizes the remaining weights.

### Valid solution

Use three separate evidence tracks:

1. **Fixed-universe PIT backtest.** Freeze the ten eligible exposures and obtain dated/vintaged inputs for every score. This can become a valid backtest only when revisions, valuations, flows and fundamental releases can be reproduced as known at each month-end.
2. **Historical event study.** Reconstruct only whitelisted material events from primary sources with publication timestamps. Label it `HISTORICAL_TIMESTAMP_RECONSTRUCTED`; use it to test reaction lags, not to claim a complete TCE portfolio return.
3. **Prospective shadow ledger.** From 2026-08-27 onward, store every monthly score and every eligible news event before subsequent prices are observed. This is the decisive validation for the thematic variant.

The thematic historical version must remain `RETROSPECTIVE_RESEARCH`; the live ledger must remain separate. No historical reconstructed event may be inserted into the live ledger.

## Intramonth news rule

Only six objective event classes can trigger a recalculation: enacted law/budget, awarded contract/order, earnings or guidance revision, regulatory approval/cancellation, programme cancellation, or credit/default event.

- Maximum one extraordinary rotation in 30 days.
- Immediate exit to cash for a thesis break or incumbent TCE below 65.
- A challenger must already have two monthly confirmations, TCE at least 70, and a lead of at least five points.
- Execution occurs only after one complete trading session; same-event-close execution is forbidden.
- News never substitutes for the second monthly confirmation of a new theme.

This rule is implementable prospectively. A credible historical test requires archived publication timestamps plus the complete contemporaneous score snapshot; news headlines alone are insufficient.

## Current conclusion

The implementation candidate is now **Three Equity Fixed: 33.0471% World, 26.6353% Momentum and 40.3176% Quality**. Trend 10 and Trend 15 remain research comparators only and require no purchase. The structural allocation remains a complete 100% portfolio. TCE deterministic results are computed separately and may support an optional additional position of at most 10%, activated only at the user's discretion; they do not automatically dilute or fund themselves from the structural portfolio.

## Investable ETF mapping as of 2026-08-27

The closest current instruments for an ETF-level validation are:

| Sleeve | Candidate | ISIN | Venue / ticker | TER | Income |
|---|---|---|---|---:|---|
| World | iShares Core MSCI World UCITS ETF | IE00B4L5Y983 | Milan / SWDA | 0.20% | Accumulating |
| Momentum | iShares Edge MSCI World Momentum Factor UCITS ETF | IE00BP3QZ825 | Milan / IWMO | 0.25% | Accumulating |
| Quality | iShares Edge MSCI World Quality Factor UCITS ETF | IE00BP3QZ601 | Milan / IWQU | 0.25% | Accumulating |

Official references: [SWDA on Borsa Italiana](https://www.borsaitaliana.it/borsa/etf/scheda/IE00B4L5Y983-ETFP.html?lang=it), [IWMO on Borsa Italiana](https://www.borsaitaliana.it/borsa/etf/listino-ufficiale.html?isin=IE00BP3QZ825&lang=it), [IWQU on Borsa Italiana](https://www.borsaitaliana.it/borsa/etf/dettaglio.html?isin=IE00BP3QZ601&mic=ETFP).

All three instruments are listed in Milan and accumulate income. They remain ETF-level implementation proxies: MSCI Momentum and MSCI Sector Neutral Quality differ from the Kenneth French BIG HiPRIOR and BIG HiOP research portfolios.

The investable structural mapping is therefore **33.0471% SWDA, 26.6353% IWMO and 40.3176% IWQU**. A deterministic TCE reading and its standalone return remain separate. If the TCE signal is not used, this three-ETF portfolio is unchanged; if it is used, its funding source and notional are a separate user decision rather than an automatic portfolio rule.
