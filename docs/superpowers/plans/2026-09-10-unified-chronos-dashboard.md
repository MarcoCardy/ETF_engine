# Unified Chronos Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose daily Chronos risk and monthly direct-ETF forecasting in one Streamlit application launched by the existing desktop shortcut.

**Architecture:** Keep `perpetual_engine/dashboard.py` as the sole Streamlit entry point and add a second Chronos renderer that calls the already tested services in `perpetual_engine/dashboard_chronos.py`. Preserve all existing configuration and archive formats; the UI only reads, edits through validated service calls, and renders existing outputs.

**Tech Stack:** Python 3.11, Streamlit 1.63, pandas, `chronos-forecasting` 2.3.1, `unittest`, `st.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-09-10-unified-chronos-dashboard-design.md`

## Global Constraints

- One process and the unchanged `Avvia Analisi ETF.cmd` launcher on `127.0.0.1:8501`.
- Chronos remains shadow/research-only and never changes actual allocations or executes trades.
- Price and macro downloads occur only through their existing explicit refresh buttons.
- Reuse `dashboard_chronos.py`; do not introduce a second forecasting engine or result schema.
- The candidate contains exactly four confirmed EUR ETFs with positive weights totalling 100%.
- Do not construct portfolio probability bands by adding marginal ETF quantiles.

---

### Task 1: Split navigation and expose the direct portfolio controls

**Files:**
- Modify: `perpetual_engine/dashboard.py:452-480`
- Test: `tests/test_dashboard.py:88-245`

**Interfaces:**
- Consumes: `load_direct_portfolios(paths: DashboardPaths, state: DashboardState) -> tuple[DirectPortfolio, DirectPortfolio]`, `save_candidate_portfolio(paths, state, rows) -> DirectPortfolio`, and `reset_candidate_portfolio(paths, state) -> DirectPortfolio`.
- Produces: `_render_chronos_portfolio(paths, state, status)` and navigation choices `Chronos rischio` / `Chronos portafoglio`.

- [ ] **Step 1: Write failing navigation and no-side-effect tests**

Update the expected sidebar choices and add an AppTest assertion that opening the direct page shows the candidate/base controls without creating `chronos_candidate_portfolio.json`:

```python
def test_chronos_portfolio_is_reachable_without_mutating_candidate(self) -> None:
    app = self.run_app("Chronos portafoglio")

    self.assertFalse(app.exception)
    self.assertEqual(app.button(key="save_chronos_candidate").label, "Salva portafoglio da studiare")
    self.assertEqual(app.button(key="reset_chronos_candidate").label, "Ripristina portafoglio base")
    self.assertEqual(app.button(key="calculate_direct_forecast").label, "Genera previsione portafoglio")
    self.assertFalse((self.root / "data" / "dashboard_v1" / "chronos_candidate_portfolio.json").exists())
```

Rename the existing Chronos UI test to select `Chronos rischio`, and expect:

```python
["Portafoglio", "ETF", "Confronti", "Chronos rischio", "Chronos portafoglio"]
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dashboard.py::DashboardAppTests::test_startup_renders_saved_data_without_network tests\test_dashboard.py::DashboardAppTests::test_chronos_portfolio_is_reachable_without_mutating_candidate -q
```

Expected: failure because the new navigation value and controls do not exist.

- [ ] **Step 3: Add the minimal direct portfolio renderer**

In `dashboard.py`, import direct services inside the renderer and convert the existing portfolio objects into editor rows:

```python
def _direct_portfolio_rows(portfolio) -> list[dict[str, object]]:
    return [{"id": item.component_id, "weight": item.weight * 100} for item in portfolio.components]


def _render_chronos_portfolio(paths: DashboardPaths, state: DashboardState, status: DashboardDataStatus | None) -> None:
    from perpetual_engine.dashboard_chronos import (
        load_direct_portfolios,
        reset_candidate_portfolio,
        save_candidate_portfolio,
    )

    st.header("Chronos portafoglio")
    st.info("Modulo sperimentale in modalità shadow: non modifica il portafoglio reale e non genera ordini.")
    candidate, base = load_direct_portfolios(paths, state)
    if "chronos_candidate_draft" not in st.session_state:
        st.session_state.chronos_candidate_draft = _direct_portfolio_rows(candidate)
    choices = list(dict.fromkeys(
        [item.component_id for item in base.components]
        + [item.study_id for item in state.catalog]
    ))
    draft = st.data_editor(
        pd.DataFrame(st.session_state.chronos_candidate_draft),
        key="chronos_candidate_editor",
        num_rows="fixed",
        column_config={
            "id": st.column_config.SelectboxColumn("ETF", options=choices, required=True),
            "weight": st.column_config.NumberColumn("Peso %", min_value=0.01, format="%.2f"),
        },
        width="stretch",
    )
    st.session_state.chronos_candidate_draft = draft.to_dict("records")
    if st.button("Salva portafoglio da studiare", key="save_chronos_candidate"):
        saved = save_candidate_portfolio(paths, state, [
            {"id": row["id"], "weight": float(row["weight"]) / 100}
            for row in st.session_state.chronos_candidate_draft
        ])
        st.session_state.chronos_candidate_draft = _direct_portfolio_rows(saved)
        st.rerun()
    if st.button("Ripristina portafoglio base", key="reset_chronos_candidate"):
        restored = reset_candidate_portfolio(paths, state)
        st.session_state.chronos_candidate_draft = _direct_portfolio_rows(restored)
        st.rerun()
    st.dataframe(pd.DataFrame(_direct_portfolio_rows(base)), hide_index=True, width="stretch")
    st.button(
        "Genera previsione portafoglio",
        key="calculate_direct_forecast",
        disabled=status is None or not status.available,
    )
```

Build `choices` from `_sources` indirectly by adding every base component and catalog ID once; do not call private service helpers. Wrap save/reset actions with the existing `_show_error` pattern.

Change the sidebar dispatch to:

```python
section = st.sidebar.radio(
    "Sezione",
    ("Portafoglio", "ETF", "Confronti", "Chronos rischio", "Chronos portafoglio"),
    key="section",
)
```

- [ ] **Step 4: Run Task 1 tests and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dashboard.py -q
```

Expected: all dashboard tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add perpetual_engine/dashboard.py tests/test_dashboard.py
git commit -m "feat: expose Chronos portfolio workspace"
```

---

### Task 2: Generate and render existing direct forecast archives

**Files:**
- Modify: `perpetual_engine/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `load_chronos_config(path) -> ChronosConfig`, `load_chronos_predictor(config) -> Callable`, `publish_direct_forecast(paths, state, predictor=...) -> DirectForecastResult`, `read_direct_forecast(path, output_root) -> tuple[manifest, rows]`, and `reconcile_direct_forecasts(paths, state) -> Path`.
- Produces: `_cached_direct_predictor(config_path, model_revision)`, `_render_direct_forecast(path, output_root)`, and session key `direct_forecast_path`.

- [ ] **Step 1: Write a failing deterministic forecast-rendering AppTest**

Create a temporary archive fixture with `portfolio_paths.csv`, `scenario_sensitivity.csv`, `volatility_snapshot.csv`, and monitoring CSVs. Patch model/service boundaries and click the real page button:

```python
with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
    "perpetual_engine.dashboard.current_data_status", return_value=self.available_status()
), patch(
    "perpetual_engine.dashboard._cached_direct_predictor", return_value=Mock()
), patch(
    "perpetual_engine.dashboard_chronos.publish_direct_forecast",
    return_value=SimpleNamespace(output_dir=forecast_dir),
), patch(
    "perpetual_engine.dashboard_chronos.read_direct_forecast",
    return_value=(manifest, etf_rows),
), patch(
    "perpetual_engine.dashboard_chronos.reconcile_direct_forecasts",
    return_value=monitoring_dir,
):
    app = AppTest.from_file(self.app_path, default_timeout=10).run()
    app.sidebar.radio(key="section").set_value("Chronos portafoglio").run()
    app.button(key="calculate_direct_forecast").click().run()

self.assertFalse(app.exception)
self.assertIn("Percorso candidato e base", [item.value for item in app.subheader])
self.assertIn("Differenza tra previsto e reale", [item.value for item in app.subheader])
self.assertEqual(app.session_state["direct_forecast_path"], str(forecast_dir))
```

Use only small CSV fixtures and mocks; no live data or model download.

- [ ] **Step 2: Run the new test and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dashboard.py::DashboardAppTests::test_direct_forecast_renders_candidate_base_and_monitoring -q
```

Expected: failure because forecast generation/rendering is not wired to the page.

- [ ] **Step 3: Wire the existing forecast services and cache**

Add the cached loader:

```python
@st.cache_resource(max_entries=2)
def _cached_direct_predictor(config_path: str, model_revision: str):
    del model_revision
    from perpetual_engine.chronos import load_chronos_predictor
    from perpetual_engine.chronos_data import load_chronos_config

    return load_chronos_predictor(load_chronos_config(Path(config_path)))
```

On `calculate_direct_forecast`, load the model revision, call `publish_direct_forecast`, store `str(result.output_dir)` in `st.session_state.direct_forecast_path`, and keep candidate/base state separate from `DashboardState`.

Render only validated archives:

```python
def _render_direct_forecast(path: Path, output_root: Path, monitoring: Path | None = None) -> None:
    from perpetual_engine.dashboard_chronos import read_direct_forecast

    manifest, rows = read_direct_forecast(path, output_root)
    scenario = st.selectbox(
        "Scenario BCE",
        ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP"),
        key="direct_scenario",
    )
    etf = pd.DataFrame(rows)
    numeric = ("horizon", "q10", "q50", "q90", "history_count")
    etf[list(numeric)] = etf[list(numeric)].apply(pd.to_numeric, errors="raise")
    st.dataframe(etf[etf["scenario"] == scenario], hide_index=True, width="stretch")
    paths = pd.read_csv(path / "portfolio_paths.csv")
    selected = paths[paths["scenario"] == scenario]
    chart = selected.pivot(index="forecast_month", columns="portfolio", values="cumulative_eur_100")
    st.subheader("Percorso candidato e base")
    st.line_chart(chart)
```

Also render the existing sensitivity and volatility CSVs. Reconcile after forecast publication and render non-empty `forecast_vs_actual.csv`, `pending_forecasts.csv`, and `live_metrics.csv` under **Differenza tra previsto e reale**. Show the manifest's warnings, cutoff, model, price-vintage, and macro-vintage identifiers. Do not infer or display joint Q10/Q90 portfolio bands.

- [ ] **Step 4: Run Task 2 tests and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dashboard.py tests\test_dashboard_chronos.py -q
```

Expected: all dashboard and direct Chronos tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add perpetual_engine/dashboard.py tests/test_dashboard.py
git commit -m "feat: render direct Chronos portfolio forecasts"
```

---

### Task 3: Update the manual and verify the one-launcher application

**Files:**
- Modify: `docs/manuale-utente.md`
- Verify unchanged: `Avvia Analisi ETF.cmd`
- Test: `tests/test_dashboard.py`, `tests/test_dashboard_chronos.py`, `tests/test_chronos.py`, `tests/test_chronos_risk.py`, `tests/test_chronos_job.py`

**Interfaces:**
- Consumes: the unified sidebar labels and controls implemented in Tasks 1–2.
- Produces: user instructions naming the two Chronos sections and the existing single desktop shortcut.

- [ ] **Step 1: Update the manual with the exact navigation and safety boundary**

Replace references to the single **Previsioni Chronos** page with:

```markdown
- **Chronos rischio**: previsione giornaliera di rendimento e volatilità, beta shadow, ablation e p-value.
- **Chronos portafoglio**: quattro ETF, scenari BCE, quantili per componente, confronto candidato/base e previsto/reale.
```

State that both sections are opened from the existing **Analisi ETF** desktop shortcut and that neither changes actual holdings.

- [ ] **Step 2: Verify the launcher still targets the sole entry point**

Run:

```powershell
Select-String -Path '.\Avvia Analisi ETF.cmd' -Pattern 'perpetual_engine\\dashboard.py','8501'
```

Expected: both patterns match once; no second launcher is added.

- [ ] **Step 3: Run focused and full regression suites**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dashboard.py tests\test_dashboard_chronos.py tests\test_chronos.py tests\test_chronos_risk.py tests\test_chronos_job.py -q
.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Start the one desktop application and inspect both pages**

Use the unchanged command:

```powershell
.venv\Scripts\python.exe -m streamlit run perpetual_engine\dashboard.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false
```

Verify `http://127.0.0.1:8501/` returns HTTP 200 and both Chronos navigation choices render without an exception. Use saved data; do not trigger a network refresh during this check.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/manuale-utente.md
git commit -m "docs: document unified Chronos application"
```
