# Perpetual Engine — Reproducible Core

This workspace contains the auditable core for portfolio VI's Distribution Rule E and Funding Protocol G, plus the deterministic Phase 2 historical backtest.

## What this component does

- preserves money calculations with exact decimal arithmetic;
- converts nominal EUR capital into real EUR capital using Italian CPI inputs;
- maintains the weekly real high-water mark and ratcheted floor;
- evaluates `GROWTH`, `NORMAL`, `PROTECTED`, `HARD_STOP`, and `DATA_INCOMPLETE`;
- calculates a tax-aware funding proposal using cash, direct government securities, and ISIN-level weighted-average cost;
- writes deterministic JSON and CSV evidence for both policy evaluation and historical research.

It does not implement live trading, broker orders, Monte Carlo, bootstrap, walk-forward optimization, or PIR search.

## Safety model

`GROWTH` always produces zero operational payout. Entering `DISTRIBUTION` requires an explicit activation date and real capital of at least EUR 800,000 on that date.

`DATA_INCOMPLETE` is a valid fail-closed result. Missing CPI, weighted-average purchase cost, tax classification, or other execution-critical data cannot trigger an automatic sale. The output records the missing fields and keeps operational payout at zero when the policy itself cannot be evaluated.

The hard floor constrains the complete portfolio outflow:

```text
net payout + tax + commission + spread <= real capital buffer
```

ETF sales use the weighted-average purchase cost for each ISIN. ETF losses are recorded separately and are not assumed to offset positive ETF income.

## Run the frozen VI snapshot

```powershell
.venv\Scripts\python.exe -m perpetual_engine evaluate `
  --config config\policy_v1.json `
  --snapshot data\fixtures\vi_2026-08-19.json `
  --out-dir outputs\latest
```

Outputs:

- `outputs/latest/result.json`: complete normalized result, warnings, trades, and SHA-256 input hashes;
- `outputs/latest/summary.csv`: one-row feed for the future weekly workbook integration.

The frozen 2026-08-19 fixture intentionally contains `null` for CPI and purchase costs that were absent from the source attachment. Its expected state is therefore `GROWTH/DATA_INCOMPLETE`, with zero payout and no proposed trades.

## Refresh official evidence and run the offline backtest

The two operations are deliberately separate. Refresh is the only networked command: it downloads the configured official or academic sources into a staging vintage, hashes and parses every required source, and publishes `current_manifest.json` only after the whole required set validates. The shipped inventory has audited offline parsers for the exact French archives, WDI `USA/WLD CM.MKT.LCAP.CD` JSON, explicitly configured FRED CSV series, Eurostat Italian HICP JSON, and the Damodaran workbooks. A failed source or pointer publication leaves the previously published manifest and vintage unchanged; a missing download can become unavailable evidence only when the source inventory labels it `validation-only`.

```powershell
.venv\Scripts\python.exe -m perpetual_engine data refresh --config config\data_sources_v1.json
```

After parsing, refresh invokes the existing World splice, FX, ERP/signal, defensive-return, and leveraged-return builders. Funding observations are admitted only in their exact regimes: DFF through 1985-12-31, one-month USD LIBOR from 1986-01-01 through 2021-08-31, and SOFR from 2021-09-01. Boundary coverage, seven-day carry, duplicates, internal gaps, and every Task 3–6 calculation error fail the transaction; observations from a source outside its regime are ignored as non-applicable.

The bundle is built contiguously from January 1979 through `expected_through`: the last complete month before retrieval, limited by all required outcome coverage. The shipped `max_bundle_staleness_days` is 45, so an older common end cannot be published. Both `current_manifest.json` and `BACKTEST_BUNDLE_V1` record identical `expected_through` and `actual_end` values. The deterministic vintage ID binds the source URLs and raw hashes, parser versions, derived hashes and schemas, and source-config hash.

The backtest command never downloads data. Its configuration points to that frozen `BACKTEST_INPUT_MANIFEST_V1` and bundle. Before any calculation or output-directory creation, it verifies every declared raw and derived SHA-256, each derived artifact's role-specific schema, the recomputed vintage identity, Windows-safe unique paths, required validation status, and the complete contiguous monthly calendar. It then constructs the Task 4–7 records and calls the same `run_backtest` implementation used by the unit tests.

```powershell
.venv\Scripts\python.exe -m perpetual_engine backtest --config config\backtest_v1.json --output outputs\backtest_v1
```

The frozen bundle must contain the exact five configured strategies' common inputs: point-in-time signal snapshots, raw public developed-world EUR returns, defensive returns, Task 6 leveraged-return ledgers, and the World/product validation evidence. Paths in both configurations resolve relative to the configuration file unless absolute. Input-manifest artifact paths and configured output paths must remain safe relative paths and cannot collide.

The output package contains:

- `signals.csv`: one point-in-time signal snapshot for each allocation month;
- `allocations.csv`: state, trigger, target weights, trades, and audit flags for all five strategies;
- `equity_curves.csv`: monthly sleeve values, NAV, and portfolio returns;
- `summary_metrics.json`: deterministic CAGR, volatility, Sharpe, drawdown, turnover, and cost summaries;
- `cost_decomposition.csv`: commission, spread, funding, World fee, leveraged fee, and residual drag kept separate;
- `world_validation.json`: public developed-world proxy validation evidence;
- `leveraged_validation.json`: independent licensed-daily, official-summary, and LWLD product evidence;
- `run_manifest.json`: frozen config/input identities, vintage and retrieval IDs, scenario labels, validation statuses, and the SHA-256 of every other output.

Repeated runs from identical frozen bytes and configuration are byte-identical, including CSV line endings. Offline outputs use the frozen `run_as_of` timestamp; they never contain the wall clock of the machine running the report.

The public market series is always labelled a public developed-world total-return proxy, never as a licensed index. Monthly 2x evidence is a long-history leveraged proxy and is not exact licensed daily history. Missing official World NAV is `FAIL_MISSING_OFFICIAL_NAV`; missing official LWLD NAV is `FAIL_MISSING_OFFICIAL_LWLD_NAV`. Other named failures include `FAIL_INSUFFICIENT_NAV_SAMPLE`, `FAIL_DAILY_VALIDATION`, `FAIL_OFFICIAL_SUMMARY_VALIDATION`, and `FAIL_LWLD_VALIDATION`. Recommendation status is `VALIDATED` only when World is `PASS`, LWLD is `PASS_LWLD_VALIDATION`, and leveraged benchmark evidence is either licensed daily `PASS_FULL_DAILY` or official-summary `PASS_PARTIAL_OFFICIAL_SUMMARY`. Any other combination is `UNVALIDATED`; absence is never reported as `PASS` or silently skipped.

Historical results are nominal and pre-tax. Tax behavior is covered only by the separate gross-fiscal Rule E unit tests; the backtest does not approximate investor taxes or distributions. All outputs are research evidence, not trading instructions, and no command connects to a broker or places an order.

## Confrontare un ETF singolo con il portafoglio principale

Gli ETF da studiare si aggiungono alla lista `studies` in `config/portfolio_p_v1.json` indicando un ID breve, nome, ticker, ISIN e una quotazione in EUR. I pesi del portafoglio principale non cambiano. Dopo l'aggiunta, l'aggiornamento prezzi acquisisce sia i quattro componenti sia gli ETF di studio:

```powershell
.venv\Scripts\python.exe -m perpetual_engine chronos portfolio-refresh --config config\portfolio_p_v1.json
```

Il confronto storico di un ETF registrato come `ID_ETF` si genera con:

```powershell
.venv\Scripts\python.exe -m perpetual_engine chronos portfolio-study-report `
  --config config\portfolio_p_v1.json `
  --study ID_ETF `
  --output outputs\studies\ID_ETF
```

Il report usa soltanto mesi conclusi e realmente comuni. Riporta rendimenti e valori cumulati, differenza di performance, rendimento annualizzato, volatilità, drawdown e correlazione. Non inventa osservazioni anteriori al lancio dell'ETF.

## Required data for an executable distribution

- Italian CPI base and current observations, with observation and publication dates;
- verified ISIN for every holding;
- weighted-average purchase cost in EUR for every potentially saleable instrument;
- exact tax classification for direct government securities and other instruments;
- current prices, price dates, quantities, cash, and operational cash minimum;
- commission and spread assumptions;
- explicit activation date after the real EUR 800,000 threshold is reached.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests are local and do not require internet access.
