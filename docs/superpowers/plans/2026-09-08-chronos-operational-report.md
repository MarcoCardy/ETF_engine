# Chronos operational report implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand Chronos shadow report backed by saved economic vintages and expose economic series, ablation, beta, utility and monthly p-values in the existing Streamlit application.

**Architecture:** Reuse the two existing immutable data stores, the daily risk report, and the detached direct-evaluation worker. Add one small economic-report adapter for point-in-time alignment and report composition; keep the Streamlit layer presentation-only.

**Tech Stack:** Python 3.12, NumPy, pandas, Streamlit, existing Chronos 2.3.1 and standard-library CSV/JSON/urllib.

**Spec:** `docs/superpowers/specs/2026-09-08-chronos-operational-report-design.md`

## Global constraints

- Chronos remains shadow-only and may not alter allocations or create orders.
- Network access occurs only from explicit refresh actions.
- Macro observations are selected by `available_at`; missing values are not future-filled.
- No new dependency is added.
- Existing report schemas remain backward compatible through additive fields/files.

---

### Task 1: Economic series inventory and point-in-time inputs

**Files:**
- Create: `perpetual_engine/economic_report.py`
- Modify: `config/data_sources_v1.json`
- Test: `tests/test_economic_report.py`

**Interfaces:**
- Produces: `economic_series_catalog(project_root: Path) -> tuple[dict[str, object], ...]`
- Produces: `load_risk_covariates(project_root: Path, dates: Sequence[np.datetime64]) -> EconomicInputs`
- Produces: `refresh_economic_data(project_root: Path) -> dict[str, str]`

- [ ] Write fixture-driven tests for current manifest discovery, source statuses, `available_at` alignment, missing-series reasons and VIX/T10YIE configuration.
- [ ] Run `python -m pytest tests/test_economic_report.py -q` and verify the tests fail because the adapter is absent.
- [ ] Implement the minimum adapter by reusing `load_observation_csv`, `align_point_in_time`, `refresh_data`, `refresh_chronos_data` and current manifests.
- [ ] Run the focused tests and the existing data-source/Chronos-data tests.

### Task 2: Beta and utility in the daily operational report

**Files:**
- Modify: `perpetual_engine/chronos_risk.py`
- Modify: `config/chronos_risk_v1.json`
- Test: `tests/test_chronos_risk.py`
- Test: `tests/test_chronos_risk_backtest.py`

**Interfaces:**
- Extends: `publish_current_risk_report(..., economic_inputs=)` while preserving the existing public call signature through an optional input.
- Adds report keys data: `economic_series`, `covariate_status`, `portfolio_comparison`, `portfolio_utility`.

- [ ] Add failing tests proving covariates reach Chronos, beta is computed from point-in-time ERP/SMA50/SMA200/3Y volatility, and production beta remains unchanged.
- [ ] Add failing tests proving utility tables use only aligned realised returns and explicitly mark unavailable M0–M3 paths.
- [ ] Implement the additive report fields and configuration-backed beta constants with no automatic portfolio action.
- [ ] Run the focused risk suites.

### Task 3: Monthly p-values and ablation publication

**Files:**
- Modify: `perpetual_engine/chronos.py`
- Modify: `perpetual_engine/dashboard_chronos.py`
- Test: `tests/test_chronos.py`
- Test: `tests/test_dashboard_chronos.py`

**Interfaces:**
- Produces: `moving_block_p_value(differences: np.ndarray, config: ChronosConfig) -> float`
- Adds: `monthly_significance.csv` to direct evaluation archives.

- [ ] Add deterministic failing tests for centred one-sided moving-block p-values and expanding month-end isolation.
- [ ] Add failing archive-schema tests showing `monthly_significance.csv` is reproducible from predictions and cannot include future origins.
- [ ] Implement the p-value helper and derive the additive CSV from existing paired losses.
- [ ] Run Chronos and direct-evaluation suites.

### Task 4: Streamlit operational report

**Files:**
- Modify: `perpetual_engine/dashboard.py`
- Modify: `perpetual_engine/chronos_job.py` only if an existing public job status is insufficient
- Test: `tests/test_dashboard.py`
- Test: `tests/test_chronos_job.py`

**Interfaces:**
- Adds browser controls: `Aggiorna dati economici`, `Genera report completo`, `Avvia valutazione ablation`.
- Displays: economic-series table, forecast/baselines, beta comparison, utility, ablation and latest month-end p-values.

- [ ] Add failing UI/source-contract tests for the controls and tables without launching a browser or real model.
- [ ] Implement native Streamlit tables and bounded model caching; do not use deprecated `use_container_width` in new code.
- [ ] Surface existing detached job progress/result and preserve the existing background-worker lock.
- [ ] Run dashboard and Windows job suites, including repeated concurrent-start coverage.

### Task 5: Real data refresh and end-to-end verification

**Files:**
- Create at runtime: immutable vintages under `data/chronos_v1` and `data/frozen`
- Create at runtime: reports under `outputs/chronos_risk_v1` and `outputs/dashboard_chronos_v1`

**Interfaces:**
- Commands: `python -m perpetual_engine chronos refresh --config config/chronos_v1.json`
- Commands: `python -m perpetual_engine data refresh --config config/data_sources_v1.json`

- [ ] Refresh the two stores from official endpoints after explicit network approval.
- [ ] Validate manifests, hashes, latest observations and the Damodaran monthly ERP workbook.
- [ ] Run one current report with the installed local Chronos model.
- [ ] Run the complete relevant test suite and record remaining point-in-time limitations.
