# Direct ETF Chronos Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Activate Chronos in the local dashboard so it forecasts four selected ETFs directly, compares the resulting candidate portfolio with the immutable base portfolio, evaluates macro-variable contribution in a persistent background job, and tracks forecasts against realized returns.

**Architecture:** Keep the fixed four-sleeve Chronos workflow intact. Add one direct-ETF service that adapts the dashboard's immutable price and macro vintages to the existing Chronos predictor and statistical helpers, plus one small Windows background-job module; the Streamlit page remains a thin renderer and action dispatcher.

**Tech Stack:** Python 3, NumPy, pandas, Streamlit 1.63.0, yfinance 1.6.0, chronos-forecasting 2.3.1, transformers 4.x, unittest/pytest, Windows stdlib process and file locking.

**Spec:** docs/superpowers/specs/2026-09-05-chronos-direct-etf-dashboard-design.md

## Global Constraints

- Reuse amazon/chronos-2 revision 29ec3766d36d6f73f0696f85560a422f50e8498c on CPU with 12 monthly horizons and quantiles 0.10, 0.50, and 0.90.
- The immutable base remains 60% SWDA.MI, 15% IWMO.MI, 15% IWQU.MI, and 10% DBMFE.PA.
- The candidate contains exactly four unique confirmed EUR ETFs with finite positive weights totaling 100% within 0.01 percentage points.
- Forecast real ETF monthly returns; never substitute WORLD, MOMENTUM, QUALITY, or TREND proxy returns.
- A component needs 12 complete monthly returns to forecast; 12-59 returns show STORICO_BREVE; contribution classification needs at least 60 returns and 12 usable origins.
- The current incomplete month is excluded and a month-end quote may be at most seven calendar days stale.
- Only ECB_DFR receives future values through ECB_FLAT, ECB_DOWN_100BP, and ECB_UP_100BP; US_TREASURY_10Y, US_CPI_YOY, BRENT_RETURN, and BIS_USD_CREDIT_YOY are historical-only.
- Show component q10/q50/q90, but calculate portfolio paths only from weighted q50 values; do not emit a portfolio quantile band.
- ETF contribution uses mean pinball loss; CANDIDATE_PORTFOLIO contribution uses MAE_Q50.
- Aggiorna dati Chronos is the only network operation in the Chronos view; startup, forecasting, evaluation, reconciliation, and rendering are offline.
- Forecast, evaluation, and monitoring artifacts are immutable and hash-bound; mutable candidate and job state are written atomically.
- One evaluation may run at a time, must survive page closure, and must become INTERRUPTED when its worker is no longer alive.
- The feature remains research-only and cannot place orders or change portfolio weights automatically.
- Do not add a dependency: use existing project packages and the Python/Windows standard library.

## File structure

- Modify perpetual_engine/chronos_data.py: allow the existing macro refresh to normalize an explicitly validated direct-ETF month window while preserving its current default behavior.
- Create perpetual_engine/dashboard_chronos.py: candidate persistence, ETF target construction, direct forecasts, evaluation, reconciliation, status, refresh orchestration, and immutable artifact readers/writers.
- Create perpetual_engine/chronos_job.py: persistent single-job lifecycle and detached worker launch.
- Modify perpetual_engine/dashboard_service.py: add Chronos paths to DashboardPaths; keep the existing historical state schema unchanged.
- Modify perpetual_engine/dashboard.py: replace the inactive Chronos notice with controls and cached-result rendering.
- Modify perpetual_engine/cli.py: add the internal direct-evaluation worker command.
- Modify tests/test_chronos.py: cover the explicit macro month window without regressing the fixed workflow.
- Create tests/test_dashboard_chronos.py: cover candidate state, direct targets, forecasts, evaluation, reconciliation, status, and refresh.
- Create tests/test_chronos_job.py: cover launch, duplicate prevention, persisted progress, interruption, failure, success, and retry.
- Modify tests/test_dashboard.py: cover the non-technical Chronos UI and prove startup remains offline.
- Create docs/manuale-utente.md only after the final interface and integration behavior are verified.

---

### Task 1: Make macro refresh accept the direct ETF month window

**Files:**
- Modify: perpetual_engine/chronos_data.py:632-720
- Modify: tests/test_chronos.py:451-690

**Interfaces:**
- Consumes: the existing load_chronos_config(), load_target_table(), normalize_covariates(), and immutable vintage publisher.
- Produces: refresh_chronos_data(config_path, *, target_months=None, fetcher=fetch_url, retrieved_at=None) -> str.

- [ ] **Step 1: Write failing tests for explicit months and default compatibility**

~~~python
def test_refresh_uses_explicit_direct_etf_months_without_loading_proxy_targets(self):
    from perpetual_engine.chronos_data import refresh_chronos_data

    direct_months = (date(2024, 1, 31), date(2024, 2, 29))
    with patch(
        "perpetual_engine.chronos_data.load_target_table",
        side_effect=AssertionError("proxy targets must not be loaded"),
    ):
        vintage_id = refresh_chronos_data(
            self.config_path,
            target_months=direct_months,
            fetcher=self.payloads.__getitem__,
            retrieved_at=self.at,
        )
    table, loaded_id = load_covariate_table(load_chronos_config(self.config_path))
    self.assertEqual(loaded_id, vintage_id)
    self.assertEqual(table.months, direct_months)

def test_refresh_rejects_invalid_explicit_months_before_downloading(self):
    fetcher = Mock(side_effect=AssertionError("network must not start"))
    with self.assertRaisesRegex(ValueError, "month-end, ordered, and contiguous"):
        refresh_chronos_data(
            self.config_path,
            target_months=(date(2025, 1, 30), date(2025, 3, 31)),
            fetcher=fetcher,
            retrieved_at=self.at,
        )
    fetcher.assert_not_called()
~~~

- [ ] **Step 2: Run the two tests and confirm the new keyword is rejected**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_chronos.py -k "explicit_direct_etf_months or invalid_explicit_months" -v
~~~

Expected: FAIL because refresh_chronos_data does not accept target_months.

- [ ] **Step 3: Add one validator and select the month source before any fetch**

~~~python
def _validated_target_months(months: Iterable[date]) -> tuple[date, ...]:
    result = tuple(months)
    if (
        not result
        or any(not isinstance(month, date) or isinstance(month, datetime) or month != _month_end(month) for month in result)
        or any(current != _next_month_end(previous) for previous, current in zip(result, result[1:]))
    ):
        raise ValueError("target months must be month-end, ordered, and contiguous")
    return result

def refresh_chronos_data(
    config_path: Path,
    *,
    target_months: Iterable[date] | None = None,
    fetcher: Callable[[str], bytes] = fetch_url,
    retrieved_at: datetime | None = None,
) -> str:
    config = load_chronos_config(config_path)
    months = (
        load_target_table(config).months
        if target_months is None
        else _validated_target_months(target_months)
    )
    # Keep the existing transaction unchanged and pass months here:
    table = normalize_covariates(config, months, source_rows)
~~~

- [ ] **Step 4: Run focused and full Chronos tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_chronos.py -v
~~~

Expected: every Chronos test passes, including the unchanged fixed-target refresh tests.

- [ ] **Step 5: Commit the compatible refresh extension**

~~~powershell
git add perpetual_engine/chronos_data.py tests/test_chronos.py
git commit -m "feat: support direct ETF macro windows"
~~~

### Task 2: Persist the separate four-ETF candidate and build direct targets

**Files:**
- Modify: perpetual_engine/dashboard_service.py:68-85
- Create: perpetual_engine/dashboard_chronos.py
- Create: tests/test_dashboard_chronos.py

**Interfaces:**
- Consumes: DashboardPaths, DashboardState, ComponentSpec, load_portfolio_config(), materialize_runtime_config(), load_current_portfolio_prices(), canonical_json(), and the current portfolio price manifest.
- Produces:
  - DirectComponent(component_id, name, ticker, isin, exchange, currency, weight).
  - DirectPortfolio(label, components).
  - EtfTargetSeries(component, months, returns, history_status, series_sha256).
  - load_direct_portfolios(paths, state) -> tuple[DirectPortfolio, DirectPortfolio], ordered candidate then base.
  - save_candidate_portfolio(paths, state, rows) -> DirectPortfolio.
  - reset_candidate_portfolio(paths, state) -> DirectPortfolio.
  - load_etf_target_snapshot(paths, state) -> DirectTargetSnapshot.

- [ ] **Step 1: Extend DashboardPaths without changing DashboardState**

~~~python
@dataclass(frozen=True)
class DashboardPaths:
    project_root: Path
    default_config: Path
    state: Path
    runtime_config: Path
    output_root: Path
    chronos_config: Path
    chronos_candidate: Path
    chronos_job: Path
    chronos_output_root: Path

    @classmethod
    def from_root(cls, root: Path) -> "DashboardPaths":
        root = root.resolve()
        return cls(
            root,
            root / "config" / "portfolio_p_v1.json",
            root / "data" / "dashboard_v1" / "state.json",
            root / "data" / "dashboard_v1" / "runtime_portfolio.json",
            root / "outputs" / "dashboard_v1" / "comparisons",
            root / "config" / "chronos_v1.json",
            root / "data" / "dashboard_v1" / "chronos_candidate_portfolio.json",
            root / "data" / "dashboard_v1" / "chronos_evaluation_job.json",
            root / "outputs" / "dashboard_chronos_v1",
        )
~~~

- [ ] **Step 2: Write failing candidate and target tests**

~~~python
def test_candidate_defaults_to_base_but_saves_separately(self):
    candidate, base = load_direct_portfolios(self.paths, self.state)
    self.assertEqual(candidate.components, base.components)
    self.assertFalse(self.paths.chronos_candidate.exists())

    rows = self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15))
    saved = save_candidate_portfolio(self.paths, self.state, rows)
    self.assertEqual(tuple(item.component_id for item in saved.components), ("SWDA", "IWMO", "IWQU", "GRID"))
    self.assertEqual(
        tuple(item.component_id for item in load_portfolio_config(self.paths.default_config).components),
        ("SWDA", "IWMO", "IWQU", "DBMFE"),
    )

def test_candidate_requires_four_unique_confirmed_eur_etfs_and_exact_weight_total(self):
    invalid = (
        self.rows(("SWDA", 0.70), ("IWMO", 0.15), ("IWQU", 0.15)),
        self.rows(("SWDA", 0.55), ("SWDA", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        self.rows(("SWDA", 0.54), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
    )
    for rows in invalid:
        with self.subTest(rows=rows), self.assertRaises(ValueError):
            save_candidate_portfolio(self.paths, self.state, rows)

def test_direct_targets_keep_each_etfs_own_history_and_share_latest_valid_origin(self):
    snapshot = load_etf_target_snapshot(self.paths, self.state)
    by_ticker = {series.component.ticker: series for series in snapshot.series}
    self.assertGreater(len(by_ticker["SWDA.MI"].returns), len(by_ticker["DBMFE.PA"].returns))
    self.assertEqual(snapshot.common_origin, date(2026, 5, 31))
    self.assertEqual(by_ticker["DBMFE.PA"].history_status, "STORICO_BREVE")
    self.assertEqual(len(snapshot.series), 5)  # Candidate GRID plus four base ETFs.
~~~

- [ ] **Step 3: Run the new module tests and confirm imports fail**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "candidate or direct_targets" -v
~~~

Expected: FAIL because dashboard_chronos.py and the new DashboardPaths fields do not exist.

- [ ] **Step 4: Implement the minimum state and return-series adapter**

Use schema DIRECT_CHRONOS_PORTFOLIO_V1 and atomic temporary-file replacement. Resolve every component only from the immutable base config or the confirmed dashboard catalog. Store name, ticker, ISIN, exchange, currency, and weight; on load, reject any identity that no longer matches its source.

~~~python
@dataclass(frozen=True)
class DirectComponent:
    component_id: str
    name: str
    ticker: str
    isin: str
    exchange: str
    currency: str
    weight: float

@dataclass(frozen=True)
class DirectPortfolio:
    label: str
    components: tuple[DirectComponent, ...]

@dataclass(frozen=True)
class EtfTargetSeries:
    component: DirectComponent
    months: tuple[date, ...]
    returns: np.ndarray
    history_status: str
    series_sha256: str

@dataclass(frozen=True)
class DirectTargetSnapshot:
    candidate: DirectPortfolio
    base: DirectPortfolio
    series: tuple[EtfTargetSeries, ...]
    common_origin: date
    price_vintage_id: str
    price_manifest_sha256: str
    retrieved_at: datetime

def _monthly_returns(prices: Mapping[date, float], staleness: int, as_of: date) -> tuple[tuple[date, ...], np.ndarray]:
    end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    selected = {}
    month = _month_end(min(prices))
    while month <= end:
        observation = _select_price(prices, month, staleness)
        if observation is not None:
            selected[month] = observation[1]
        month = _next_month_end(month)
    returns = []
    months = []
    for month in sorted(selected):
        previous = _month_end(date(month.year, month.month, 1) - timedelta(days=1))
        if previous in selected:
            months.append(month)
            returns.append(selected[month] / selected[previous] - 1.0)
    return tuple(months), np.asarray(returns, dtype=float)
~~~

Reject fewer than 12 returns, label 12-59 as STORICO_BREVE, and label 60 or more as SUFFICIENT_HISTORY. Build the unique union by ticker and ISIN, retain each series' own start, and choose the latest month present in every union series as common_origin. Bind the canonical price manifest bytes and each series' canonical CSV bytes to SHA-256.

- [ ] **Step 5: Run candidate, target, and historical dashboard service tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py tests/test_dashboard_service.py -v
~~~

Expected: PASS.

- [ ] **Step 6: Commit the direct ETF state and target boundary**

~~~powershell
git add perpetual_engine/dashboard_service.py perpetual_engine/dashboard_chronos.py tests/test_dashboard_chronos.py
git commit -m "feat: prepare direct ETF Chronos targets"
~~~

### Task 3: Publish direct ETF forecasts and the two central portfolio paths

**Files:**
- Modify: perpetual_engine/dashboard_chronos.py
- Modify: tests/test_dashboard_chronos.py

**Interfaces:**
- Consumes: load_chronos_config(), load_covariate_table(), ecb_scenarios(), load_chronos_predictor(), _atomic_snapshot(), DirectTargetSnapshot, and both DirectPortfolio values from Task 2.
- Produces:
  - DirectForecastResult(forecast_id, output_dir, common_origin).
  - run_direct_scenario_forecasts(config, snapshot, covariates, predictor) -> tuple[ForecastRow, ...].
  - publish_direct_forecast(paths, state, *, predictor=None, issued_at=None) -> DirectForecastResult.
  - read_direct_forecast(path, output_root) -> validated manifest and rows.

~~~python
@dataclass(frozen=True)
class DirectForecastResult:
    forecast_id: str
    output_dir: Path
    common_origin: date
    candidate_sha256: str
    base_sha256: str
~~~

- [ ] **Step 1: Write failing tests for batching, warnings, identities, and central paths**

~~~python
def test_direct_forecast_batches_unique_etfs_once_and_never_emits_portfolio_bounds(self):
    calls = []
    def predictor(items, prediction_length, quantile_levels):
        calls.append(items)
        return [np.tile(np.asarray([[[0.0, 0.01, 0.02]]]), (1, 12, 1)) for _ in items]

    result = publish_direct_forecast(
        self.paths,
        self.state_with_grid_candidate(),
        predictor=predictor,
        issued_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    forecasts = list(csv.DictReader((result.output_dir / "etf_forecast.csv").open()))
    paths = list(csv.DictReader((result.output_dir / "portfolio_paths.csv").open()))
    self.assertEqual(len(calls), 1)
    self.assertEqual(len(calls[0]), 15)  # Five unique ETFs times three scenarios.
    self.assertEqual(len(forecasts), 5 * 3 * 12)
    self.assertEqual(set(paths[0]), {
        "portfolio", "scenario", "forecast_month", "horizon", "central_return", "cumulative_eur_100",
    })
    self.assertNotIn("q10", paths[0])
    self.assertNotIn("q90", paths[0])

def test_direct_forecast_rejects_one_invalid_component_without_renormalizing(self):
    self.remove_price_history("GRID.MI")
    with self.assertRaisesRegex(ValueError, "GRID.MI"):
        publish_direct_forecast(self.paths, self.state_with_grid_candidate(), predictor=self.fake_predictor)
    self.assertFalse((self.paths.chronos_output_root / "forecasts").exists())
~~~

- [ ] **Step 2: Run the focused forecast tests and confirm they fail**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "direct_forecast" -v
~~~

Expected: FAIL because direct forecast publication is not implemented.

- [ ] **Step 3: Implement one model load and one prediction call**

For each ETF, align its own months with the macro table up to common_origin. Build three items per ETF containing a one-row target array, matching past covariates, and only the twelve-value ECB_DFR future scenario. Flatten the items into one predictor call and require one finite (1, 12, 3) result per item with ordered quantiles.

~~~python
def run_direct_scenario_forecasts(config, snapshot, covariates, predictor):
    work = []
    for series in snapshot.series:
        aligned = _align_one_etf(series, covariates, snapshot.common_origin)
        last_rate = float(aligned.covariates.values[0, -1])
        scenarios = ecb_scenarios(last_rate, config.scenario_basis_points, config.prediction_length)
        for scenario in SCENARIO_NAMES:
            work.append((series, scenario, {
                "target": aligned.returns.reshape(1, -1).astype(np.float32),
                "past_covariates": aligned.past_covariates,
                "future_covariates": {"ECB_DFR": scenarios[scenario]},
            }))
    arrays = predictor(
        [item for _series, _scenario, item in work],
        prediction_length=12,
        quantile_levels=[0.1, 0.5, 0.9],
    )
    return _validated_direct_forecast_rows(work, arrays, snapshot.common_origin)
~~~

Calculate candidate and base central monthly returns from component q50 rows and compound from EUR 100. Calculate down/up sensitivity relative to ECB_FLAT. Preserve component q10/q50/q90 only.

- [ ] **Step 4: Bind and atomically publish the forecast**

Compute forecast_id from canonical JSON containing model/config identity, both ordered portfolio definitions, all series hashes, price manifest hash, macro vintage ID, common origin, and scenario definitions. Publish exactly etf_forecast.csv, portfolio_paths.csv, scenario_sensitivity.csv, volatility_snapshot.csv, and manifest.json under forecasts/forecast_id. Validate byte-identical reuse and reject collisions.

- [ ] **Step 5: Run all direct forecast tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "forecast or portfolio_path or sensitivity" -v
~~~

Expected: PASS.

- [ ] **Step 6: Commit direct forecast publication**

~~~powershell
git add perpetual_engine/dashboard_chronos.py tests/test_dashboard_chronos.py
git commit -m "feat: publish direct ETF Chronos forecasts"
~~~

### Task 4: Reconcile archived forecasts with realized ETF returns

**Files:**
- Modify: perpetual_engine/dashboard_chronos.py
- Modify: tests/test_dashboard_chronos.py

**Interfaces:**
- Consumes: read_direct_forecast(), the current validated price vintage, and archived portfolio definitions.
- Produces:
  - reconcile_direct_forecasts(paths, state) -> Path.
  - immutable monitoring snapshots under outputs/dashboard_chronos_v1/monitoring.
  - publish_direct_forecast() invokes reconciliation after successful publication.

- [ ] **Step 1: Write failing realized, pending, and archive-integrity tests**

~~~python
def test_reconciliation_records_actual_minus_q50_and_keeps_future_rows_pending(self):
    forecast = self.publish_known_forecast(q50=0.01)
    self.add_realized_return("SWDA.MI", date(2026, 6, 30), 0.03)
    monitoring = reconcile_direct_forecasts(self.paths, self.state)
    actual = self.csv_rows(monitoring / "forecast_vs_actual.csv")
    row = next(item for item in actual if item["symbol"] == "SWDA.MI" and item["horizon"] == "1")
    self.assertAlmostEqual(float(row["signed_error"]), 0.02)
    self.assertAlmostEqual(float(row["absolute_error"]), 0.02)
    self.assertAlmostEqual(float(row["squared_error"]), 0.0004)
    self.assertIn("2026-07-31", {item["forecast_month"] for item in self.csv_rows(monitoring / "pending_forecasts.csv")})

def test_reconciliation_uses_archived_weights_for_candidate_and_base(self):
    forecast = self.publish_known_forecast()
    self.change_current_candidate_weights()
    monitoring = reconcile_direct_forecasts(self.paths, self.state)
    portfolio_row = next(
        row for row in self.csv_rows(monitoring / "forecast_vs_actual.csv")
        if row["scope"] == "CANDIDATE_PORTFOLIO"
    )
    self.assertEqual(portfolio_row["portfolio_sha256"], forecast.candidate_sha256)

def test_reconciliation_rejects_tampered_forecast_before_publishing_monitoring(self):
    forecast = self.publish_known_forecast()
    (forecast.output_dir / "etf_forecast.csv").write_text("tampered", encoding="utf-8")
    with self.assertRaisesRegex(ValueError, "hash"):
        reconcile_direct_forecasts(self.paths, self.state)
~~~

- [ ] **Step 2: Run reconciliation tests and confirm they fail**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "reconciliation" -v
~~~

Expected: FAIL because the direct archive reader and monitoring publisher do not exist.

- [ ] **Step 3: Implement strict archive reading and row reconciliation**

For ETF rows, write q10/q50/q90 and interval_hit. For CANDIDATE_PORTFOLIO and BASE_PORTFOLIO rows, write archived weighted actual and q50, while q10, q90, and interval_hit are empty. Use these columns:

~~~python
MONITORING_COLUMNS = (
    "forecast_id", "issued_at", "origin", "scope", "portfolio_sha256",
    "scenario", "symbol", "isin", "forecast_month", "horizon",
    "q10", "q50", "q90", "actual", "signed_error",
    "absolute_error", "squared_error", "interval_hit",
)
~~~

Validate every archive's direct child path, exact file set, manifest schema, generated hashes, deterministic ID, row count, ETF identity, origin, horizon, month, scenario, finite values, and quantile order before using it.

- [ ] **Step 4: Publish an immutable monitoring snapshot**

Compute monitoring_id from the current price vintage manifest hash and ordered included forecast manifest hashes. Write forecast_vs_actual.csv, pending_forecasts.csv, live_metrics.csv, and manifest.json atomically. Metrics include count, bias, MAE, RMSE, and component interval coverage; portfolio interval coverage stays empty.

- [ ] **Step 5: Run reconciliation and forecast tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "reconciliation or direct_forecast" -v
~~~

Expected: PASS.

- [ ] **Step 6: Commit prospective tracking**

~~~powershell
git add perpetual_engine/dashboard_chronos.py tests/test_dashboard_chronos.py
git commit -m "feat: track direct ETF forecasts against actuals"
~~~

### Task 5: Evaluate the five variables for ETFs and the candidate portfolio

**Files:**
- Modify: perpetual_engine/dashboard_chronos.py
- Modify: tests/test_dashboard_chronos.py

**Interfaces:**
- Consumes: evaluation_variants(), load_chronos_predictor(), pinball_loss(), moving_block_interval(), _atomic_snapshot(), and Task 2 target snapshots.
- Produces:
  - prepare_direct_evaluation_request(paths, state) -> Path.
  - evaluate_direct_request(project_root, request_path, *, predictor=None, progress=None) -> Path.
  - immutable evaluation requests and results under outputs/dashboard_chronos_v1.

- [ ] **Step 1: Write failing tests for origin safety and insufficient history**

~~~python
def test_evaluation_uses_last_at_most_36_origins_with_36_past_and_12_future_months(self):
    request = prepare_direct_evaluation_request(self.paths, self.long_history_state)
    seen_lengths = []
    def predictor(items, prediction_length, quantile_levels):
        seen_lengths.extend(item["target"].shape[1] for item in items)
        return self.fake_quantiles(items)
    output = evaluate_direct_request(self.root, request, predictor=predictor)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    self.assertEqual(manifest["evaluation_origins"]["count"], 36)
    self.assertGreaterEqual(min(seen_lengths), 36)

def test_short_history_publishes_only_insufficient_history_without_loading_model(self):
    request = prepare_direct_evaluation_request(self.paths, self.short_history_state)
    output = evaluate_direct_request(
        self.root,
        request,
        predictor=Mock(side_effect=AssertionError("model must not run")),
    )
    rows = self.csv_rows(output / "covariate_contribution.csv")
    self.assertEqual({row["classification"] for row in rows}, {"INSUFFICIENT_HISTORY"})
    self.assertEqual({row["loss_metric"] for row in rows}, {"PINBALL", "MAE_Q50"})
~~~

- [ ] **Step 2: Write failing contribution and volatility tests**

~~~python
def test_etf_uses_pinball_and_portfolio_uses_mae_q50_for_contribution(self):
    output = evaluate_direct_request(self.root, self.request, predictor=self.variant_predictor)
    rows = self.csv_rows(output / "covariate_contribution.csv")
    self.assertEqual({row["loss_metric"] for row in rows if row["scope"] != "CANDIDATE_PORTFOLIO"}, {"PINBALL"})
    self.assertEqual({row["loss_metric"] for row in rows if row["scope"] == "CANDIDATE_PORTFOLIO"}, {"MAE_Q50"})
    conditional = [row for row in rows if row["comparison"] == "CONDITIONAL"]
    self.assertEqual({row["classification"] for row in conditional}, {"USEFUL", "HARMFUL", "INCONCLUSIVE"})

def test_volatility_uses_only_twelve_months_before_each_origin(self):
    output = evaluate_direct_request(self.root, self.request, predictor=self.fake_predictor)
    rows = self.csv_rows(output / "volatility_diagnostics.csv")
    first = rows[0]
    self.assertAlmostEqual(
        float(first["trailing_volatility_12m"]),
        statistics.stdev(self.returns_before(first["symbol"], first["origin"])[-12:]) * math.sqrt(12),
    )
~~~

- [ ] **Step 3: Run focused evaluation tests and confirm they fail**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "evaluation or contribution or volatility" -v
~~~

Expected: FAIL because direct evaluation is not implemented.

- [ ] **Step 4: Freeze and validate the request**

Write requests/evaluation_id/request.json with DIRECT_CHRONOS_EVALUATION_REQUEST_V1. Bind model/config hash, candidate definition and hash, the exact four ETF series and hashes, price vintage and manifest hash, macro vintage ID and manifest hash, eligible origins, and output root. Store only project-relative paths, reject path escape, and recheck every hash in the worker before model loading.

- [ ] **Step 5: Implement bounded walk-forward variants**

For each of the at most 36 common eligible origins, each of four candidate ETFs, and each variant from evaluation_variants(REQUIRED_V1_COVARIATES), build a one-series item with its own expanding history. Never include observations after the origin. Predict in deterministic chunks of at most 36 items while loading the model once. Add ZERO_RETURN_BASELINE rows without a model call. Do not add the oracle-future-rate variant to this dashboard evaluation.

Emit:

~~~python
PREDICTION_COLUMNS = (
    "variant", "origin", "scope", "symbol", "isin",
    "forecast_month", "horizon", "actual", "q10", "q50", "q90",
)
CONTRIBUTION_COLUMNS = (
    "covariate", "comparison", "scope", "horizon", "loss_metric",
    "loss_without", "loss_with", "improvement", "ci_low", "ci_high", "classification",
)
~~~

For ETF scopes, bootstrap per-origin mean pinball-loss differences. For CANDIDATE_PORTFOLIO, aggregate component q50 and actual returns with archived weights and bootstrap per-origin absolute-error differences. Use block six, 2,000 resamples, seed 42, and 95% intervals. Publish predictions.csv, metrics.csv, covariate_contribution.csv, volatility_diagnostics.csv, and manifest.json under evaluations/evaluation_id.

- [ ] **Step 6: Run all direct evaluation tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py -k "evaluation or contribution or volatility" -v
~~~

Expected: PASS.

- [ ] **Step 7: Commit direct evaluation**

~~~powershell
git add perpetual_engine/dashboard_chronos.py tests/test_dashboard_chronos.py
git commit -m "feat: evaluate Chronos variables for ETF portfolios"
~~~

### Task 6: Run evaluation as one persistent background job

**Files:**
- Create: perpetual_engine/chronos_job.py
- Modify: perpetual_engine/cli.py:37-60, 105-140
- Create: tests/test_chronos_job.py
- Modify: tests/test_cli.py

**Interfaces:**
- Consumes: prepare_direct_evaluation_request() and evaluate_direct_request().
- Produces:
  - EvaluationJob(job_id, state, progress, request_path, result_path, message, started_at, updated_at).
  - start_evaluation_job(paths, state, *, launcher=subprocess.Popen) -> EvaluationJob.
  - read_evaluation_job(paths, *, now=None) -> EvaluationJob | None.
  - run_evaluation_worker(project_root, request_path, state_path, lock_path, *, predictor=None) -> int.
  - CLI command chronos dashboard-evaluate-worker.

~~~python
@dataclass(frozen=True)
class EvaluationJob:
    job_id: str
    state: str
    progress: float
    request_path: Path
    result_path: Path | None
    message: str
    started_at: datetime
    updated_at: datetime
~~~

- [ ] **Step 1: Write failing job lifecycle tests**

~~~python
def test_start_writes_request_and_launches_hidden_detached_worker_once(self):
    launcher = Mock(return_value=SimpleNamespace(pid=4321))
    first = start_evaluation_job(self.paths, self.state, launcher=launcher)
    second = start_evaluation_job(self.paths, self.state, launcher=launcher)
    self.assertEqual(first.job_id, second.job_id)
    launcher.assert_called_once()
    command = launcher.call_args.args[0]
    self.assertEqual(command[:4], [sys.executable, "-m", "perpetual_engine", "chronos"])
    self.assertIn("dashboard-evaluate-worker", command)
    self.assertTrue(first.request_path.is_file())

def test_dead_running_worker_becomes_interrupted_and_can_retry(self):
    self.write_job(state="RUNNING", updated_at=self.old_time, lock_held=False)
    interrupted = read_evaluation_job(self.paths, now=self.now)
    self.assertEqual(interrupted.state, "INTERRUPTED")
    launcher = Mock(return_value=SimpleNamespace(pid=9876))
    retried = start_evaluation_job(self.paths, self.state, launcher=launcher)
    self.assertEqual(retried.state, "STARTING")
    launcher.assert_called_once()

def test_worker_persists_success_and_sanitized_failure(self):
    with patch("perpetual_engine.chronos_job.evaluate_direct_request", return_value=self.result):
        self.assertEqual(run_evaluation_worker(*self.worker_args), 0)
    self.assertEqual(read_evaluation_job(self.paths).state, "SUCCEEDED")

    with patch("perpetual_engine.chronos_job.evaluate_direct_request", side_effect=ValueError("bad input")):
        self.assertEqual(run_evaluation_worker(*self.next_worker_args), 2)
    failed = read_evaluation_job(self.paths)
    self.assertEqual(failed.state, "FAILED")
    self.assertNotIn("Traceback", failed.message)
~~~

- [ ] **Step 2: Run job tests and confirm module import failure**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_chronos_job.py tests/test_cli.py -k "evaluation_job or dashboard_evaluate_worker" -v
~~~

Expected: FAIL because chronos_job.py and the worker command do not exist.

- [ ] **Step 3: Implement atomic state and a worker-held liveness lock**

Use the existing publication lock to serialize state transitions. The worker opens a one-byte job-specific lock file and holds an msvcrt exclusive lock until it writes SUCCEEDED or FAILED. read_evaluation_job treats STARTING as live for a 30-second launch grace period; after that, STARTING or RUNNING with an unlocked worker file becomes INTERRUPTED.

~~~python
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "INTERRUPTED"})

def _job_payload(job: EvaluationJob) -> bytes:
    return canonical_json({
        "schema_version": "DIRECT_CHRONOS_JOB_V1",
        "job_id": job.job_id,
        "state": job.state,
        "progress": job.progress,
        "request_path": _relative(job.request_path),
        "result_path": None if job.result_path is None else _relative(job.result_path),
        "message": job.message,
        "started_at": job.started_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    })
~~~

The progress callback atomically updates only progress, message, and updated_at for the same job ID. An old worker must not overwrite a newer retry state.

- [ ] **Step 4: Launch the detached worker with no inherited console**

~~~python
command = [
    sys.executable, "-m", "perpetual_engine", "chronos",
    "dashboard-evaluate-worker",
    "--project-root", str(paths.project_root),
    "--request", str(request_path),
    "--state", str(paths.chronos_job),
    "--lock", str(lock_path),
]
process = launcher(
    command,
    cwd=paths.project_root,
    stdin=subprocess.DEVNULL,
    stdout=log_handle,
    stderr=subprocess.STDOUT,
    close_fds=True,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
)
~~~

Validate that project root, request, state, lock, log, and result paths remain within their expected project directories. The worker is offline and does not call refresh functions.

- [ ] **Step 5: Add and test the internal CLI command**

~~~python
worker = chronos_commands.add_parser("dashboard-evaluate-worker")
worker.add_argument("--project-root", type=Path, required=True)
worker.add_argument("--request", type=Path, required=True)
worker.add_argument("--state", type=Path, required=True)
worker.add_argument("--lock", type=Path, required=True)
~~~

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_chronos_job.py tests/test_cli.py -v
~~~

Expected: PASS.

- [ ] **Step 6: Commit persistent evaluation jobs**

~~~powershell
git add perpetual_engine/chronos_job.py perpetual_engine/cli.py tests/test_chronos_job.py tests/test_cli.py
git commit -m "feat: run Chronos evaluation in background"
~~~

### Task 7: Activate the non-technical Chronos dashboard

**Files:**
- Modify: perpetual_engine/dashboard_chronos.py
- Modify: perpetual_engine/dashboard.py:11-28, 81-116, 308-339
- Modify: tests/test_dashboard_chronos.py
- Modify: tests/test_dashboard.py:19-215

**Interfaces:**
- Consumes: all direct service functions from Tasks 2-6 and existing dashboard price refresh/state/catalog functions.
- Produces:
  - ChronosStatus(model_available, price_status, macro_available, macro_vintage_id, macro_last_month, job).
  - current_direct_chronos_status(paths, state) -> ChronosStatus.
  - refresh_direct_chronos_data(paths, state, *, price_downloader=None, macro_fetcher=fetch_url, retrieved_at=None, progress=None) -> ChronosStatus.
  - _render_chronos(paths, state, historical_status) in dashboard.py.

~~~python
@dataclass(frozen=True)
class ChronosStatus:
    model_available: bool
    model_reason: str | None
    price_status: DashboardDataStatus
    macro_available: bool
    macro_vintage_id: str | None
    macro_last_month: date | None
    macro_reason: str | None
    job: EvaluationJob | None
~~~

- [ ] **Step 1: Write failing status and explicit-refresh tests**

~~~python
def test_chronos_status_reads_cache_and_vintages_without_network_or_model_load(self):
    with patch("perpetual_engine.dashboard_chronos.refresh_dashboard_data", side_effect=AssertionError), patch(
        "perpetual_engine.dashboard_chronos.refresh_chronos_data", side_effect=AssertionError
    ), patch(
        "perpetual_engine.dashboard_chronos.load_chronos_predictor", side_effect=AssertionError
    ):
        status = current_direct_chronos_status(self.paths, self.state)
    self.assertTrue(status.price_status.available)
    self.assertEqual(status.macro_vintage_id, self.macro_vintage_id)

def test_explicit_chronos_refresh_updates_prices_then_macro_for_direct_months(self):
    events = []
    with patch("perpetual_engine.dashboard_chronos.refresh_dashboard_data", side_effect=lambda *a, **k: events.append("prices")), patch(
        "perpetual_engine.dashboard_chronos.refresh_chronos_data", side_effect=lambda *a, **k: events.append(("macro", k["target_months"]))
    ):
        refresh_direct_chronos_data(self.paths, self.state)
    self.assertEqual(events[0], "prices")
    self.assertEqual(events[1][0], "macro")
    self.assertGreaterEqual(len(events[1][1]), 12)
~~~

- [ ] **Step 2: Replace the inactive AppTest with the approved controls**

~~~python
def test_chronos_section_has_separate_refresh_forecast_and_background_evaluation(self):
    app = self.run_app("Previsioni Chronos")
    self.assertFalse(app.exception)
    self.assertEqual(app.button(key="refresh_chronos").label, "Aggiorna dati Chronos")
    self.assertEqual(app.button(key="generate_chronos_forecast").label, "Genera previsione")
    self.assertEqual(app.button(key="evaluate_chronos_variables").label, "Valuta variabili")
    self.assertIn("Portafoglio base", [header.value for header in app.subheader])
    self.assertIn("Portafoglio da studiare", [header.value for header in app.subheader])

def test_chronos_startup_does_not_refresh_load_model_or_start_worker(self):
    with patch("perpetual_engine.dashboard.refresh_direct_chronos_data", side_effect=AssertionError), patch(
        "perpetual_engine.dashboard.publish_direct_forecast", side_effect=AssertionError
    ), patch(
        "perpetual_engine.dashboard.start_evaluation_job", side_effect=AssertionError
    ):
        app = self.run_app("Previsioni Chronos")
    self.assertFalse(app.exception)
~~~

- [ ] **Step 3: Run focused service and AppTests and confirm failure**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py tests/test_dashboard.py -k "chronos" -v
~~~

Expected: FAIL because the inactive notice is still present.

- [ ] **Step 4: Implement cached status and the combined explicit refresh**

Resolve the model cache with huggingface_hub local_files_only behavior without constructing Chronos2Pipeline. Read and validate price and macro pointers locally. On refresh, first call refresh_dashboard_data(), then load the direct ETF target union and pass its contiguous min-to-max month range to refresh_chronos_data(target_months=...). Report price and macro failures separately and preserve their last valid vintages.

- [ ] **Step 5: Render the candidate editor and action buttons**

Use a fixed four-row editor. ETF selection choices come from immutable base components plus confirmed catalog entries. Only weight is freely editable; changing an ETF selects a complete canonical identity. Save to chronos_candidate_portfolio.json and offer Ripristina composizione Chronos with separate confirmation.

Disable:

- Aggiorna dati Chronos while a foreground refresh is active.
- Genera previsione unless model, prices, macro, candidate, and all union target histories validate.
- Valuta variabili while a verified evaluation job is STARTING or RUNNING.

Button handlers call exactly one service action and rerun after success. They do not infer weight changes from forecast output.

- [ ] **Step 6: Render only validated cached artifacts**

Show per-ETF q10/q50/q90 tables and scenario charts, candidate and base EUR 100 central paths, ECB sensitivity, STORICO_BREVE warnings, volatility, contribution labels with plain Italian explanations, background progress/failure/retry, and prospective forecast errors. Load the latest validated artifact paths returned by the service; never scan and render an unvalidated directory.

Provide download buttons for each CSV and manifest named in the spec. Label missing portfolio bands explicitly: the central path is shown, but no combined probability band is calculated.

- [ ] **Step 7: Move the general historical refresh out of the Chronos section**

Select the sidebar section before rendering its status controls. Keep Aggiorna dati for Portafoglio, ETF, and Confronti. In Previsioni Chronos show only Aggiorna dati Chronos so the user cannot accidentally update prices without the macro vintage from that view.

- [ ] **Step 8: Run all dashboard tests**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_chronos.py tests/test_dashboard_service.py tests/test_dashboard.py -v
~~~

Expected: PASS with no AppTest exception and no network or model work on startup.

- [ ] **Step 9: Commit the activated dashboard**

~~~powershell
git add perpetual_engine/dashboard_chronos.py perpetual_engine/dashboard.py tests/test_dashboard_chronos.py tests/test_dashboard.py
git commit -m "feat: activate direct ETF Chronos dashboard"
~~~

### Task 8: Verify the real local workflow and write the Italian manual

**Files:**
- Create: docs/manuale-utente.md
- Modify only if verification exposes a defect: the smallest source and matching test from Tasks 1-7.

**Interfaces:**
- Consumes: the completed local dashboard, desktop launcher, cached Chronos model, explicit refresh, forecast, evaluation job, CSV downloads, and monitoring outputs.
- Produces: a verified non-technical manual matching the final interface and a recorded real integration outcome in the final handoff.

- [ ] **Step 1: Run the full automated suite before real data work**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest -q
~~~

Expected: all tests pass with zero failures.

- [ ] **Step 2: Verify the local model and available disk without loading the dashboard**

Run:

~~~powershell
.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; print(snapshot_download('amazon/chronos-2', revision='29ec3766d36d6f73f0696f85560a422f50e8498c', local_files_only=True))"
Get-PSDrive -PSProvider FileSystem | Select-Object Name,Free,Used
~~~

Expected: the frozen revision resolves locally and the chosen output drive has sufficient free space. No download occurs.

- [ ] **Step 3: Perform one explicit real Chronos data refresh**

Start the local page from Analisi ETF.lnk, open Previsioni Chronos, and press Aggiorna dati Chronos once. Verify both the ETF price and macro status show the new retrieval time; inspect both current manifests and confirm their referenced vintage hashes.

- [ ] **Step 4: Run one real direct forecast**

Use an approved candidate containing SWDA.MI, IWMO.MI, IWQU.MI, and GRID.MI with weights totaling 100%. Press Genera previsione and verify:

- every unique candidate/base ETF has 36 rows: three scenarios times twelve horizons;
- candidate and base central paths each have 36 rows;
- DBMFE/GRID short-history labels match their actual counts;
- no portfolio q10 or q90 columns exist;
- every CSV hash matches manifest.json;
- the dashboard downloads open as valid CSV files.

- [ ] **Step 5: Run one real background contribution evaluation**

Press Valuta variabili, record the UTC start time, close the browser page, reopen it, and verify the same job remains STARTING or RUNNING. Wait for its terminal state without starting a duplicate. Record elapsed time, terminal state, result path, origin count, classification counts, and manifest hash verification. If the computer or worker is stopped deliberately, verify INTERRUPTED appears and Riprova valutazione starts one replacement job.

- [ ] **Step 6: Verify prospective reconciliation**

Run reconciliation against the current price vintage. Confirm future rows are pending. If a forecast month has become complete, confirm signed_error equals actual minus q50 for one ETF row and one candidate-portfolio row. Do not fabricate a realized month merely to make the real record non-empty.

- [ ] **Step 7: Write the final Italian manual from the verified interface**

Create docs/manuale-utente.md with this exact chapter structure:

~~~markdown
# Manuale utente — Analisi ETF e Chronos

## 1. Che cosa fa il programma
### 1.1 Analisi storica
### 1.2 Previsioni Chronos
### 1.3 Limiti e uso esclusivamente di ricerca

## 2. Avvio e chiusura
### 2.1 Avvio dal desktop
### 2.2 Chiusura della pagina e del programma

## 3. Primo utilizzo guidato
### 3.1 Controllare lo stato dei dati
### 3.2 Aggiornare i dati
### 3.3 Generare la prima previsione

## 4. Portafoglio base e portafoglio da studiare
### 4.1 Composizione base non modificabile
### 4.2 Selezionare quattro ETF
### 4.3 Inserire simbolo o ISIN
### 4.4 Impostare e salvare i pesi
### 4.5 Ripristinare la composizione predefinita

## 5. Aggiornamento dei dati Chronos
### 5.1 Dati dei prezzi
### 5.2 Tasso BCE, Treasury USA, inflazione USA, petrolio e liquidità
### 5.3 Cosa accade in caso di errore

## 6. Generazione e lettura delle previsioni
### 6.1 Scenari BCE invariato, discesa e rialzo
### 6.2 Previsioni dei singoli ETF
### 6.3 Percorsi del portafoglio da studiare e del portafoglio base
### 6.4 Significato di q10, q50 e q90
### 6.5 Storico breve e assenza della banda di portafoglio

## 7. Valutazione delle variabili
### 7.1 Avvio del calcolo in background
### 7.2 Chiusura e riapertura della pagina
### 7.3 Utile, dannosa, inconcludente e storico insufficiente
### 7.4 Riavvio di un calcolo interrotto

## 8. Volatilità e qualità delle previsioni
### 8.1 Volatilità storica
### 8.2 Ampiezza degli intervalli
### 8.3 Differenza tra previsto e reale

## 9. Esportazione dei risultati
### 9.1 File CSV disponibili
### 9.2 Manifesti e provenienza dei dati

## 10. Risoluzione dei problemi
### 10.1 Modello non disponibile
### 10.2 Dati mancanti o non aggiornati
### 10.3 Simbolo o ISIN non riconosciuto
### 10.4 Pesi non validi
### 10.5 Storico insufficiente
### 10.6 Valutazione interrotta

## 11. Glossario
~~~

Fill every paragraph with the exact final button labels, visible warnings, interpretation limits, and corrective actions observed during Steps 3-6. Include no shell commands in the normal first-use path.

- [ ] **Step 8: Verify the manual and rerun the full suite**

Run:

~~~powershell
rg -n "^## [0-9]+\\.|^### [0-9]+\\." docs/manuale-utente.md
rg -n "Aggiorna dati Chronos|Genera previsione|Valuta variabili|STORICO_BREVE|USEFUL|HARMFUL|INCONCLUSIVE|INSUFFICIENT_HISTORY|q10|q50|q90|previsto|reale" docs/manuale-utente.md
.venv\Scripts\python.exe -m pytest -q
git diff --check
~~~

Expected: all eleven chapters and their listed paragraphs are present, every interface term is documented, all tests pass, and diff checking reports no errors.

- [ ] **Step 9: Commit the verified manual**

~~~powershell
git add docs/manuale-utente.md
git commit -m "docs: add Chronos dashboard user manual"
~~~

### Task 9: Final repository and local-application checkpoint

**Files:**
- No planned source changes.
- Preserve existing untracked data under data/portfolio_p_v1, outputs/dro_v1, and outputs/studies.

**Interfaces:**
- Consumes: every task commit and the installed desktop shortcut.
- Produces: final verification evidence and a clean implementation handoff.

- [ ] **Step 1: Run the complete automated verification once more**

Run:

~~~powershell
.venv\Scripts\python.exe -m pytest -q
git diff --check
~~~

Expected: zero test failures and no whitespace errors.

- [ ] **Step 2: Verify the desktop entry and local-only binding**

Resolve Analisi ETF.lnk and confirm its target is Avvia Analisi ETF.cmd in the project. Start it and verify the health endpoint at http://127.0.0.1:8501/_stcore/health returns HTTP 200. Confirm the launcher still passes --server.address=127.0.0.1.

- [ ] **Step 3: Review the complete implementation diff against the specification**

Run:

~~~powershell
git log --oneline --decorate -12
git diff --stat 225433e..HEAD
git status --short
~~~

Expected: only planned source, test, and manual files are committed; the three pre-existing data/output directories remain untracked and untouched.

- [ ] **Step 4: Record the verified final checkpoint**

Run:

~~~powershell
git rev-parse HEAD
~~~

Expected: one commit ID identifying the verified implementation and manual handoff. If Step 3 exposed a defect, return to the task that owns that file, add its failing regression test, make the smallest correction, rerun that task's verification, commit the exact files changed there, and then repeat Tasks 8 and 9.
