# Chronos operational report design

## Goal

Expose one reproducible, on-demand shadow report that combines the saved ETF prices, the saved economic vintages, Chronos forecasts, ablation evidence, beta comparison, portfolio utility metrics, and month-by-month statistical significance. Data is downloaded only through explicit refresh controls.

## Data contract

- `data/chronos_v1` remains the immutable five-series monthly Chronos covariate store (ECB deposit rate, US 10Y nominal yield, Brent return, BIS USD credit growth, US CPI growth).
- `data/frozen` remains the immutable general economic store and adds VIX and the US 10Y breakeven series. It also contains DGS10, DFII10, EUR/USD and Damodaran ERP.
- Each source is listed in the browser with classification, source URL, latest observation, availability timestamp, retrieval timestamp, quality status and vintage identity.
- Current/revised FRED and Damodaran history is labelled as a research limitation. Missing licensed MSCI forward earnings yield is reported as unavailable and never imputed.
- Slow series are carried forward only after `available_at`. Inputs are sliced to a common finite history; no future observation is used.

## Operational flow

1. `Aggiorna dati economici` refreshes both immutable stores and never runs as a side effect of report generation.
2. `Genera report completo` uses the current saved price vintage and current saved macro vintages.
3. The daily risk forecast receives all usable aligned economic and factor covariates, while listing rejected series and reasons.
4. The report appends the experimental beta comparison. Fast and slow trend are SWDA above SMA50 and SMA200; the rule is explicit and shadow-only.
5. Historical utility metrics are shown for the saved base and candidate portfolios. Model M0–M3 utility is shown only when a temporally valid evaluation exists; absent evidence is labelled unavailable.
6. The existing detached monthly evaluation remains the ablation engine. Its outputs are surfaced in the report and extended with expanding, month-end, one-sided p-values.

## Statistical meaning

For every completed evaluation month, compute paired loss improvements using only origins available through that month. A moving-block bootstrap, centred under the null, estimates the one-sided p-value for `H1: candidate loss < baseline loss`. Horizons with insufficient matured origins are labelled `INSUFFICIENT_HISTORY`. A current forecast displays the latest matured p-value; no p-value is claimed for an unrealised future outcome.

## Safety and compatibility

Chronos remains `SHADOW`. No allocation, trade or production beta is changed. Existing report fields and files remain readable; new sections and CSV files are additive. Report generation is offline and deterministic for fixed price, macro, configuration and model inputs.
