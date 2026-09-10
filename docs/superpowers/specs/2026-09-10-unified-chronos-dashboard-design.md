# Unified Chronos dashboard design

## Goal

Expose the existing daily risk and direct ETF forecasting workflows through one local Streamlit application, one process, and the existing `Analisi ETF` desktop shortcut. The merge is a user-interface integration: the forecasting engines and their archived outputs remain separate, reproducible modules.

## Current state

- `Avvia Analisi ETF.cmd` already launches `perpetual_engine/dashboard.py` on localhost port 8501.
- The current dashboard exposes portfolio editing, ETF discovery, historical comparisons, daily Chronos shadow risk, economic-data refresh, beta comparison, portfolio utility, ablation, and monthly p-values.
- `perpetual_engine/dashboard_chronos.py` already implements the missing direct ETF workflow: candidate/base portfolios, monthly ECB scenarios, per-ETF forecasts, portfolio paths, volatility snapshots, reconciliation, and background evaluation.
- The former `codex/chronos-direct-etf-dashboard` branch is already an ancestor of `main`; no Git branch merge or second executable is required.

## Selected approach

Keep the existing single-file Streamlit entry point and add two explicit navigation choices:

1. **Chronos rischio** — the current daily shadow-risk report and its diagnostics.
2. **Chronos portafoglio** — the existing direct four-ETF monthly scenario workflow.

The existing radio navigation is retained because the application is small and already tested. A migration to `st.navigation`, a second Streamlit server, or an embedded iframe would add complexity without improving the requested result.

## Chronos portafoglio page

The page will:

- load the saved candidate portfolio and immutable default base portfolio through `load_direct_portfolios`;
- allow exactly four confirmed EUR ETFs and editable weights totalling 100%, using the existing catalog;
- save or restore the candidate through the existing validated service functions;
- generate a forecast from saved price and macro vintages through `publish_direct_forecast`;
- show Q10, Q50, and Q90 for each ETF under the unchanged, falling, and rising ECB-rate scenarios;
- show the candidate and base Q50 paths without presenting marginal ETF quantiles as a joint portfolio probability band;
- show scenario sensitivity, volatility diagnostics, short-history warnings, forecast cutoff, model version, and vintage identities;
- expose forecast-versus-real reconciliation when matured observations exist;
- keep the existing background ablation and monthly p-value results available from the unified application.

The candidate forecast portfolio remains separate from the actual portfolio state. Neither page modifies allocations, executes trades, or produces BUY/SELL instructions.

## Data and model flow

`Aggiorna dati` remains the only price refresh action. `Aggiorna dati economici` remains the only macro refresh action. Forecast buttons use only the currently published local vintages.

The Streamlit process caches the direct Chronos predictor as a resource, as it already does for the daily-risk predictor. Forecast creation continues to use the existing atomic, hashed archive under `outputs/dashboard_chronos_v1`; no new result format is introduced.

## Error handling

Missing price or macro vintages, invalid weights, insufficient history, model-loading failures, and archive-validation failures use the dashboard's existing user message plus expandable technical details. A failed forecast does not replace a valid archive or portfolio configuration.

## Compatibility

- The desktop shortcut and launcher command remain unchanged.
- Existing configuration, data, and output schemas remain unchanged.
- The daily report is moved only at the navigation-label level; its calculations remain unchanged.
- Existing direct forecast archives remain readable.

## Tests and acceptance

Add AppTest coverage proving that both Chronos sections are reachable from the same entry point and that the direct page renders its candidate/base controls. Use deterministic mocks for model inference and external data.

Acceptance requires:

1. one desktop shortcut starts one Streamlit server;
2. both Chronos sections are available in that application;
3. a deterministic direct ETF forecast can be generated and rendered;
4. the base path remains visible beside the candidate path;
5. existing dashboard, Chronos, and launcher tests pass;
6. no network request or portfolio mutation occurs merely by opening either page.
