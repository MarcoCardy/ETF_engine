# Chronos-2 Shadow Risk Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily, probabilistic Chronos-2 risk layer that measures incremental out-of-sample value without changing production allocations.

**Architecture:** Preserve the existing monthly Chronos and VI engines. Add a focused daily shadow-risk module over the existing immutable adjusted-price vintages, with pure financial calculations, a reusable Chronos adapter, walk-forward evidence, and UI/CLI readers. Production and experimental beta outputs remain separate.

**Tech Stack:** Python 3.12, NumPy, chronos-forecasting 2.3.1, Streamlit, pytest.

**Spec:** User-approved Chronos-2 forward-risk requirements dated 2026-09-07.

## Audit summary

- Existing Chronos is monthly, forecasts `WORLD/MOMENTUM/QUALITY/TREND` for 12 months, and already supports past/known-future covariates, scenarios, walk-forward pinball evaluation, ablation, immutable archives, monitoring, and background jobs.
- Existing portfolio ingestion freezes validated daily adjusted closes, but the visible Chronos dashboard is still a placeholder.
- The requested daily `sigma20/sigma60/EWMA/Kelly/betaCeiling` engine does not exist. The current production allocation engine uses different monthly ERP/crisis/TIPS rules and must remain unchanged.
- `chronos-forecasting==2.3.1` and `Chronos2Pipeline` are installed; `amazon/chronos-2` is cached locally. Q10/Q25/Q50/Q75/Q90, multivariate targets, past covariates and future covariates are supported. CPU is available; CUDA is not.
- Point-in-time `available_at` selection exists, but CPI, Damodaran ERP and other downloaded macro histories are current-vintage research data. Publication-date alignment limits ordinary look-ahead, but revision leakage remains unless archival vintages are sourced.
- Current price history begins in 2009 for SWDA and later for the factor ETFs; 2008 evaluation needs a separately validated daily developed-world proxy.

## Global constraints

- Shadow mode only: no allocation changes, orders, or BUY/SELL output.
- Daily horizons: 5, 10 and 20 sessions; 60 only after the core evaluation is sound.
- Quantiles: 0.10, 0.25, 0.50, 0.75 and 0.90; they are predictive quantiles, not classical confidence intervals.
- No path-dependent drawdown probabilities without validated coherent sample paths.
- Monthly macro observations are carried forward only after `available_at`; never interpolate missing or future values.
- Interpret sigma-risk weights as `0.25*sigma20 + 0.35*sigma60 + 0.40*chronos_vol`.

### Task 1: Stabilize the existing background publication

**Files:** `perpetual_engine/dashboard_chronos.py`, `tests/test_chronos_job.py`

- [ ] Keep output directories pinned against rename during request/result publication.
- [ ] Verify focused background/dashboard tests and the full suite.
- [ ] Commit the pre-existing background-job work separately.

### Task 2: Daily risk calculations and shadow portfolio state

**Files:** create `perpetual_engine/chronos_risk.py`, `config/chronos_risk_v1.json`, `config/portfolio_snapshot_2026-08-17.json`, `tests/test_chronos_risk.py`

- [ ] Test first: daily returns, forward realized volatility, sigma20/60, EWMA(0.94), blended sigma, Kelly, betaDesired, beta ceilings, LWLD 2x exposure, and cutoff isolation.
- [ ] Implement pure validated functions and strict configuration/snapshot loaders.
- [ ] Keep production and Chronos beta results in distinct immutable records.

### Task 3: Chronos probabilistic forecast and reproducibility

**Files:** modify `perpetual_engine/chronos_risk.py`, `tests/test_chronos_risk.py`

- [ ] Test first: single/multivariate inputs, past covariates, five-quantile output schema and one pipeline load per run.
- [ ] Forecast trailing H-session return and volatility targets at `T+H`, so contexts end at cutoff T.
- [ ] Store cutoff, model/package/revision, context, target, covariates, dataset/config hashes and outputs.

### Task 4: Walk-forward, calibration, ablation and utility

**Files:** modify `perpetual_engine/chronos_risk.py`, create `tests/test_chronos_risk_backtest.py`

- [ ] Test first: expanding/rolling temporal isolation and all five anti-leakage requirements.
- [ ] Add volatility/return baselines, forecast metrics, quantile calibration, feature-group ablations A-G and risk-regime diagnostics.
- [ ] Compare M0/M1/M2/M3 on drawdown, volatility, downside deviation, Sharpe, Sortino, Calmar, CVaR, turnover and risk-reduction errors.

### Task 5: CLI, dashboard and documentation

**Files:** modify `perpetual_engine/cli.py`, `perpetual_engine/dashboard.py`, `README.md`; create CLI/UI tests.

- [ ] Add separate offline `chronos risk-forecast` and `chronos risk-evaluate` commands.
- [ ] Replace the Chronos placeholder with the requested market, risk, diagnostics and beta comparison, without trade recommendations.
- [ ] Run real local inference, validate artifacts, run the full suite, and document exact commands plus remaining data/vintage limits.

### Deferred until trustworthy data exists

- MSCI World forward earnings yield, VIX and long daily factor-relative histories are not fabricated or scraped into backtests.
- True drawdown probabilities and GARCH are excluded until coherent paths or measured incremental value justify them.
