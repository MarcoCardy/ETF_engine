# Local ETF Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a private Streamlit dashboard launched from the Windows desktop for editing the current ETF portfolio, explicitly refreshing cached prices, adding verified ETFs, and comparing one ETF with the current portfolio.

**Architecture:** Keep `config/portfolio_p_v1.json` as the immutable default and store mutable user choices in `data/dashboard_v1/state.json`. A small service module materializes the existing monitor's runtime catalog, calls the existing transactional refresh/calculation code, and publishes immutable comparison reports; a single Streamlit file renders those services without owning financial logic.

**Tech Stack:** Python 3.12, Streamlit 1.63.0, yfinance 1.6.0, NumPy, standard-library JSON/CSV/path handling, pytest/unittest, PowerShell and Windows shortcuts.

**Spec:** `docs/superpowers/specs/2026-09-04-local-etf-dashboard-design.md`

## Global Constraints

- Bind the app only to `127.0.0.1`; never expose it to the LAN or Internet.
- Perform no network request during app startup. Network access is limited to the explicit `Aggiorna dati` and ETF search actions.
- Preserve `config/portfolio_p_v1.json` as the immutable `60% SWDA / 15% IWMO / 15% IWQU / 10% DBMFE` default.
- Store mutable state under `data/dashboard_v1/` and publish historical results under `outputs/dashboard_v1/`.
- A valid current portfolio has positive finite weights totaling 100% within `0.01` percentage points.
- A failed state save or data refresh must preserve the last valid state and vintage.
- Reuse `perpetual_engine.portfolio_monitor`; do not duplicate its price validation, month alignment, or portfolio-return arithmetic in the UI.
- Chronos remains visible but inactive in v1. No forecast command or model load is allowed from the dashboard.
- Add only `streamlit==1.63.0`; do not add a database, plotting library, web framework, scheduler, authentication layer, or cloud service.
- Preserve all pre-existing uncommitted GRID and portfolio-monitor work. Stage and commit only the paths named in each task.
- Execution precondition: checkpoint the current GRID/portfolio-monitor changes before Task 1, then execute from that checkpoint so an isolated worktree cannot omit them and later README commits cannot absorb old hunks.

## File Map

- Modify `perpetual_engine/portfolio_monitor.py`: expose current-price loading and a pure reusable single-ETF comparison while retaining existing CLI report behavior.
- Modify `tests/test_portfolio_monitor.py`: lock the reusable comparison contract and project-root override.
- Create `perpetual_engine/dashboard_service.py`: dashboard state, runtime catalog, ETF discovery, refresh orchestration, and immutable dashboard report publication.
- Create `tests/test_dashboard_service.py`: state, discovery, refresh failure, comparison and publication tests.
- Create `perpetual_engine/dashboard.py`: one Streamlit entry point with four sidebar sections.
- Create `tests/test_dashboard.py`: headless Streamlit rendering and no-network startup checks.
- Modify `requirements.txt`: pin Streamlit 1.63.0.
- Create `Avvia Analisi ETF.cmd`: local dashboard launcher.
- Create `scripts/install_dashboard_shortcut.ps1`: create the user's desktop shortcut.
- Modify `.gitignore`: ignore mutable dashboard state and generated reports.
- Modify `README.md`: non-technical install, launch, update, add-ETF and comparison instructions.

---

### Task 1: Reusable historical comparison in the existing monitor

**Files:**
- Modify: `perpetual_engine/portfolio_monitor.py:37-82,110-200,562-820`
- Modify: `tests/test_portfolio_monitor.py:112-181,252-575`

**Interfaces:**
- Consumes: existing `ComponentSpec`, `StudySpec`, `PortfolioConfig`, `PortfolioRow`, `portfolio_rows()` and `_load_current()`.
- Produces: `valid_isin()`, `HistoricalComparisonRow`, `HistoricalComparisonSummary`, `HistoricalComparison`, `load_current_portfolio_prices()`, and `compare_single_etf()` for Tasks 3-5.
- Preserves: `load_portfolio_config(path)` and `write_etf_study_report(path, study_id, output)` remain valid for all existing callers.

- [ ] **Step 1: Write failing tests for a runtime config outside `config/` and for the richer comparison**

Add tests that call the new public interfaces without writing a report:

```python
def test_runtime_config_can_use_an_explicit_project_root(self):
    from perpetual_engine.portfolio_monitor import load_portfolio_config

    runtime = self.root / "data" / "dashboard_v1" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(self.config_path.read_bytes())

    config = load_portfolio_config(runtime, project_root=self.root)

    self.assertEqual(config.project_root, self.root.resolve())
    self.assertEqual(config.data_root, (self.root / "data" / "portfolio_p_v1").resolve())


def test_comparison_exposes_volatility_drawdown_and_component_contributions(self):
    from perpetual_engine.portfolio_monitor import (
        StudySpec,
        compare_single_etf,
        load_current_portfolio_prices,
        refresh_portfolio_prices,
    )

    payload = config_payload()
    payload["studies"] = [{**STUDY}]
    self.write_config(payload)
    dates = months(14)
    prices = {
        **self.payloads(),
        "TEST.MI": daily_bytes(list(prices_from_returns(dates, (0.04, -0.01, 0.02, 0.01, -0.03, 0.025, 0.015, -0.01, 0.005, 0.03, -0.02, 0.01, 0.02)).items())),
    }
    refresh_portfolio_prices(self.config_path, downloader=prices.__getitem__, retrieved_at=datetime(2026, 3, 2, tzinfo=timezone.utc))
    config, raw, manifest = load_current_portfolio_prices(self.config_path)

    result = compare_single_etf(config, raw, config.studies[0], as_of=datetime.fromisoformat(manifest["retrieved_at"]).date())

    self.assertEqual(result.component_ids, ("SWDA", "IWMO", "IWQU", "DBMFE"))
    self.assertEqual(len(result.rows), 13)
    self.assertAlmostEqual(sum(result.rows[0].component_contributions), result.rows[0].portfolio_return)
    self.assertAlmostEqual(sum(result.rows[-1].component_cumulative_contributions), result.rows[-1].portfolio_cumulative_value / 100.0 - 1.0)
    self.assertIsNone(result.rows[10].etf_trailing_volatility_12m)
    self.assertIsNotNone(result.rows[11].etf_trailing_volatility_12m)
    self.assertLessEqual(result.rows[4].etf_drawdown, 0.0)
    self.assertEqual(result.summary.count, 13)
```

- [ ] **Step 2: Run the focused tests and verify the missing-interface failures**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_portfolio_monitor.py -k "explicit_project_root or comparison_exposes" -v
```

Expected: FAIL because `project_root`, `load_current_portfolio_prices`, and `compare_single_etf` do not exist yet.

- [ ] **Step 3: Add the minimal public comparison data model and project-root override**

Add these frozen records beside `PortfolioRow`:

```python
@dataclass(frozen=True)
class HistoricalComparisonRow:
    month: date
    etf_return: float
    portfolio_return: float
    etf_cumulative_value: float
    portfolio_cumulative_value: float
    etf_drawdown: float
    portfolio_drawdown: float
    etf_trailing_volatility_12m: float | None
    portfolio_trailing_volatility_12m: float | None
    rolling_correlation_12m: float | None
    component_contributions: tuple[float, ...]
    component_cumulative_contributions: tuple[float, ...]


@dataclass(frozen=True)
class HistoricalComparisonSummary:
    first_month: date
    last_month: date
    count: int
    etf_cumulative_return: float
    portfolio_cumulative_return: float
    etf_annualized_return: float
    portfolio_annualized_return: float
    etf_annualized_volatility: float | None
    portfolio_annualized_volatility: float | None
    etf_max_drawdown: float
    portfolio_max_drawdown: float
    correlation: float | None
    etf_winning_months: int
    portfolio_winning_months: int
    tied_months: int


@dataclass(frozen=True)
class HistoricalComparison:
    study: StudySpec
    component_ids: tuple[str, ...]
    rows: tuple[HistoricalComparisonRow, ...]
    summary: HistoricalComparisonSummary
```

Change the loader without changing its default behavior:

```python
def load_portfolio_config(path: Path, *, project_root: Path | None = None) -> PortfolioConfig:
    path = path.resolve()
    project_root = (project_root.resolve() if project_root is not None else path.parent.parent.resolve())
    if not path.is_relative_to(project_root):
        raise ValueError("portfolio configuration escapes project root")
```

Expose the existing ISIN checksum safely as `valid_isin(value: str) -> bool`; it returns `False` rather than raising for a value that does not match `[A-Z]{2}[A-Z0-9]{10}`. Use it from both the existing config loader and Task 3 discovery.

Add the same optional keyword to refresh so a runtime config under `data/dashboard_v1/` still resolves project-relative data correctly:

```python
def refresh_portfolio_prices(
    path: Path,
    *,
    project_root: Path | None = None,
    downloader: Callable | None = None,
    retrieved_at: datetime | None = None,
) -> str:
    config = load_portfolio_config(path, project_root=project_root)
```

Expose the validated current data:

```python
def load_current_portfolio_prices(
    path: Path,
    *,
    project_root: Path | None = None,
) -> tuple[PortfolioConfig, dict[str, dict[date, float]], Mapping[str, Any]]:
    config = load_portfolio_config(path, project_root=project_root)
    raw, manifest, _ = _load_current(config)
    return config, raw, MappingProxyType(dict(manifest))
```

Implement `compare_single_etf(config, raw, study, *, reference_components=None, as_of=None)` by moving the existing common-month loop out of `write_etf_study_report()`. Use `replace(config, components=...)` and `portfolio_rows()` for both sides. Calculate each monthly contribution exactly as:

```python
contributions = tuple(
    component.weight * component_return
    for component, component_return in zip(reference_config.components, reference_row.component_returns)
)
```

Calculate each rolling volatility only after 12 monthly returns:

```python
etf_volatility = statistics.stdev(etf_returns[-12:]) * math.sqrt(12) if len(etf_returns) >= 12 else None
portfolio_volatility = statistics.stdev(portfolio_returns[-12:]) * math.sqrt(12) if len(portfolio_returns) >= 12 else None
```

Initialize cumulative attribution with one zero per reference component, then maintain it so its components reconcile to the portfolio cumulative return:

```python
cumulative_contributions = tuple(0.0 for _ in reference_config.components)
cumulative_contributions = tuple(
    previous * (1.0 + portfolio_return) + monthly
    for previous, monthly in zip(cumulative_contributions, contributions)
)
```

Count ETF wins, portfolio wins, and exact ties from the aligned monthly returns and store all three counts in the summary.

Refactor `write_etf_study_report()` to call the new function and serialize the same existing CSV columns and manifest keys, so the CLI contract does not change.

- [ ] **Step 4: Run monitor tests and confirm compatibility**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_portfolio_monitor.py -v
```

Expected: all tests PASS, including the existing byte-stability, CLI and collision tests.

- [ ] **Step 5: Commit Task 1 only**

```powershell
git add perpetual_engine/portfolio_monitor.py tests/test_portfolio_monitor.py
git commit -m "refactor: expose reusable ETF comparisons"
```

---

### Task 2: Mutable dashboard state without changing the frozen default

**Files:**
- Create: `perpetual_engine/dashboard_service.py`
- Create: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: `load_portfolio_config()`, `ComponentSpec`, `StudySpec`, `canonical_json()`.
- Produces: `CatalogEtf`, `DashboardState`, `load_dashboard_state()`, `save_dashboard_state()`, `reset_dashboard_state()`, `materialize_runtime_config()`, `state_sha256()`.
- State schema: `ETF_DASHBOARD_STATE_V1` with exact top-level keys `schema_version`, `components`, and `catalog`.

- [ ] **Step 1: Write failing state lifecycle tests**

Create a temporary project with this exact reusable fixture at the top of `tests/test_dashboard_service.py`:

```python
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from perpetual_engine.io import canonical_json
from tests.test_portfolio_monitor import STUDY, config_payload, daily_bytes, months, prices_from_returns


class DashboardServiceFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.default_config = self.root / "config" / "portfolio_p_v1.json"
        self.default_config.parent.mkdir(parents=True)
        payload = config_payload()
        payload["studies"] = [{**STUDY}]
        self.default_config.write_bytes(canonical_json(payload))
        self.default_bytes = self.default_config.read_bytes()
        self.state_path = self.root / "data" / "dashboard_v1" / "state.json"
        self.retrieved_at = datetime(2026, 3, 2, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def price_payloads(self):
        dates = months(14)
        returns = {
            "SWDA.MI": (0.01,) * 13,
            "IWMO.MI": (0.02,) * 13,
            "IWQU.MI": (0.005,) * 13,
            "DBMFE.PA": (-0.002, 0.01) * 6 + (-0.002,),
            "TEST.MI": (0.03, -0.01) * 6 + (0.03,),
        }
        return {
            ticker: daily_bytes(list(prices_from_returns(dates, changes).items()))
            for ticker, changes in returns.items()
        }
```

Place the state tests in `class DashboardStateTests(DashboardServiceFixture)`:

```python
def test_first_load_copies_default_and_reset_restores_it(self):
    from perpetual_engine.dashboard_service import load_dashboard_state, reset_dashboard_state, save_dashboard_state

    state = load_dashboard_state(self.default_config, self.state_path)
    self.assertEqual(tuple(item.component_id for item in state.components), ("SWDA", "IWMO", "IWQU", "DBMFE"))
    self.assertEqual(tuple(item.weight for item in state.components), (0.60, 0.15, 0.15, 0.10))
    changed = replace(state, components=(replace(state.components[0], weight=1.0),))
    save_dashboard_state(self.state_path, changed, default_config_path=self.default_config)

    restored = reset_dashboard_state(self.default_config, self.state_path)

    self.assertEqual(restored, state)
    self.assertEqual(self.default_config.read_bytes(), self.default_bytes)


def test_invalid_save_preserves_last_valid_state(self):
    from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

    original = load_dashboard_state(self.default_config, self.state_path)
    invalid = replace(original, components=(replace(original.components[0], weight=0.50),))

    with self.assertRaisesRegex(ValueError, "100%"):
        save_dashboard_state(self.state_path, invalid, default_config_path=self.default_config)

    self.assertEqual(load_dashboard_state(self.default_config, self.state_path), original)
```

Also cover duplicate ticker, duplicate ISIN, non-finite/zero weights, unknown holding identity, malformed JSON, and the `0.01` percentage-point tolerance boundary.

- [ ] **Step 2: Run the new tests and verify the module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py -k "first_load or invalid_save" -v
```

Expected: FAIL with `ModuleNotFoundError: perpetual_engine.dashboard_service`.

- [ ] **Step 3: Implement the strict state records and atomic save**

Use the existing component and study records:

```python
@dataclass(frozen=True)
class CatalogEtf:
    study_id: str
    name: str
    ticker: str
    isin: str
    exchange: str
    quote_currency: str
    identity_source_url: str


@dataclass(frozen=True)
class DashboardState:
    components: tuple[ComponentSpec, ...]
    catalog: tuple[CatalogEtf, ...]


def state_sha256(state: DashboardState) -> str:
    return hashlib.sha256(_state_bytes(state)).hexdigest()
```

`_state_bytes()` must emit canonical JSON with component keys `id,ticker,isin,quote_currency,weight` and catalog keys `id,name,ticker,isin,exchange,quote_currency,identity_source_url`. Validate holdings against the union of immutable default components and catalog entries. For entries copied from the versioned config, derive `Borsa Italiana` for `.MI` and `Euronext Paris` for `.PA`; newly searched entries retain the provider's exchange text. Compare the total using decimals:

```python
total = sum((Decimal(str(item.weight)) for item in state.components), Decimal("0"))
if abs(total - Decimal("1")) > Decimal("0.0001"):
    raise ValueError("portfolio weights must total 100% within 0.01 percentage points")
```

Write to a named temporary file in `state_path.parent`, flush and replace `state.json` only after validation. A malformed existing state raises `ValueError("dashboard state is malformed")` and is never replaced automatically.

- [ ] **Step 4: Materialize only the data-source catalog**

Implement:

```python
def materialize_runtime_config(
    default_config_path: Path,
    state: DashboardState,
    runtime_config_path: Path,
    *,
    project_root: Path,
) -> Path:
```

Read the default JSON, leave its four `components` and fixed weights unchanged, and replace only `studies` with `state.catalog` mapped to the six keys accepted by `StudySpec` (drop only `exchange`). When the mapped catalog equals the default studies, write the original default bytes so existing matching vintages remain usable. Otherwise write `canonical_json(payload)`. Validate the result with:

```python
load_portfolio_config(runtime_config_path, project_root=project_root)
```

Use the same temporary-file/replace pattern as state saving.

- [ ] **Step 5: Run the complete service state tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py -v
```

Expected: all state and runtime-config tests PASS.

- [ ] **Step 6: Commit Task 2 only**

```powershell
git add perpetual_engine/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat: persist editable dashboard portfolio"
```

---

### Task 3: Explicit ETF search and confirmed catalog changes

**Files:**
- Modify: `perpetual_engine/dashboard_service.py`
- Modify: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: yfinance only inside `search_etfs()` after a user action.
- Produces: `EtfCandidate`, `search_etfs()`, `add_catalog_entry()`, `remove_catalog_entry()`.
- Preserves: app startup and state loading import no yfinance network object and issue no request.

- [ ] **Step 1: Write deterministic discovery tests with injected providers**

Add fakes whose `quotes` contain one ETF and one equity, and whose ticker returns metadata plus an ISIN:

```python
def test_search_accepts_symbol_or_isin_and_keeps_only_verified_eur_etfs(self):
    from perpetual_engine.dashboard_service import search_etfs

    quotes = [
        {"symbol": "GRID.MI", "quoteType": "ETF", "longname": "First Trust Smart Grid", "exchange": "MIL"},
        {"symbol": "NOTETF.MI", "quoteType": "EQUITY", "longname": "Not an ETF", "exchange": "MIL"},
    ]

    class FakeTicker:
        def get_history_metadata(self):
            return {"currency": "EUR", "exchangeName": "Milan"}

        def get_isin(self):
            return "IE000J80JTL1"

    for query in ("GRID.MI", "IE000J80JTL1"):
        with self.subTest(query=query):
            found = search_etfs(
                query,
                search_factory=lambda value, **kwargs: SimpleNamespace(quotes=quotes),
                ticker_factory=lambda symbol: FakeTicker(),
            )
            self.assertEqual(tuple(item.ticker for item in found), ("GRID.MI",))
            self.assertEqual(found[0].isin, "IE000J80JTL1")
            self.assertEqual(found[0].quote_currency, "EUR")
```

Add tests proving that blank input, USD listings, missing/`-` ISIN, invalid ISIN checksum, mismatched ISIN query, duplicate symbol/ISIN, and a removal of an ETF currently held all raise clear `ValueError`s without changing state. Assert that the confirmed catalog entry preserves `exchange == "Milan"` across save and reload.

- [ ] **Step 2: Run the discovery tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py -k "search or catalog" -v
```

Expected: FAIL because discovery and catalog functions do not exist.

- [ ] **Step 3: Implement lazy yfinance discovery and confirmation records**

Add:

```python
@dataclass(frozen=True)
class EtfCandidate:
    name: str
    ticker: str
    isin: str
    exchange: str
    quote_currency: str
    identity_source_url: str
```

Implement this signature:

```python
def search_etfs(
    query: str,
    *,
    search_factory: Callable[..., Any] | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
) -> tuple[EtfCandidate, ...]:
```

Import yfinance inside the function only when either factory is absent. Call `yf.Search(query, max_results=8, news_count=0, lists_count=0, include_cb=False)`. Keep only `quoteType == "ETF"`; for each candidate read `get_history_metadata()` and `get_isin()`, require EUR and a valid ISIN, deduplicate by ticker and ISIN, and sort by ticker. When the input matches the ISIN shape, retain only exact ISIN matches. Set the persistent source URL with standard-library quoting:

```python
identity_source_url = f"https://finance.yahoo.com/quote/{quote(ticker, safe='.^=-')}"
```

`add_catalog_entry(state, candidate)` derives a stable ID from the complete ticker by replacing non-alphanumeric characters with `_` (`CSPX.MI` becomes `CSPX_MI`), validates it with the monitor's existing ID rules, converts the candidate to `CatalogEtf`, and returns a new state. A derived-ID collision with another identity is an explicit error. `remove_catalog_entry(state, study_id)` refuses removal when the catalog ticker or ISIN occurs in `state.components`.

- [ ] **Step 4: Run all dashboard service tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py -v
```

Expected: all tests PASS with no real network access.

- [ ] **Step 5: Commit Task 3 only**

```powershell
git add perpetual_engine/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat: add verified ETF catalog search"
```

---

### Task 4: Refresh orchestration and immutable dashboard comparisons

**Files:**
- Modify: `perpetual_engine/dashboard_service.py`
- Modify: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: `materialize_runtime_config()`, `refresh_portfolio_prices()`, `load_current_portfolio_prices()`, `compare_single_etf()`, and `chronos._atomic_snapshot()`.
- Produces: `DashboardDataStatus`, `current_data_status()`, `refresh_dashboard_data()`, `DashboardReport`, `publish_dashboard_comparison()`.
- Output: `outputs/dashboard_v1/comparisons/<comparison_id>/comparison_monthly.csv`, `comparison_summary.csv`, `component_contributions.csv`, and `manifest.json`.

- [ ] **Step 1: Write failing refresh and publication tests**

Add a test that first publishes a valid vintage, then injects a failing downloader and verifies the pointer bytes remain unchanged:

```python
def test_failed_refresh_preserves_the_current_vintage(self):
    from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data

    paths = DashboardPaths.from_root(self.root)
    state = load_dashboard_state(self.default_config, self.state_path)
    first = refresh_dashboard_data(paths, state, downloader=self.price_payloads().__getitem__, retrieved_at=self.retrieved_at)
    pointer = first.pointer_path.read_bytes()

    with self.assertRaisesRegex(ValueError, "SWDA.MI"):
        refresh_dashboard_data(paths, state, downloader=lambda ticker: (_ for _ in ()).throw(ValueError(ticker)))

    self.assertEqual(first.pointer_path.read_bytes(), pointer)
```

Add a report test with a non-default valid weight vector. Refresh its fixture first, then assert that:

```python
from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, publish_dashboard_comparison, refresh_dashboard_data

paths = DashboardPaths.from_root(self.root)
state = load_dashboard_state(self.default_config, self.state_path)
refresh_dashboard_data(paths, state, downloader=self.price_payloads().__getitem__, retrieved_at=self.retrieved_at)
report = publish_dashboard_comparison(paths, state, "TEST")
self.assertEqual(report.output_dir.name, report.comparison_id)
self.assertTrue((report.output_dir / "comparison_monthly.csv").is_file())
self.assertTrue((report.output_dir / "comparison_summary.csv").is_file())
self.assertTrue((report.output_dir / "component_contributions.csv").is_file())
self.assertEqual(publish_dashboard_comparison(paths, state, "TEST").comparison_id, report.comparison_id)
```

Verify the monthly contribution columns sum to `portfolio_return`, the output manifest hashes every CSV, a weight change produces a different ID, and a repeated identical call preserves byte-identical files.

- [ ] **Step 2: Run the orchestration tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py -k "refresh or publication or report" -v
```

Expected: FAIL because the orchestration interfaces do not exist.

- [ ] **Step 3: Add one paths record and local status reader**

Use one explicit paths record to keep UI constants out of calculations:

```python
@dataclass(frozen=True)
class DashboardPaths:
    project_root: Path
    default_config: Path
    state: Path
    runtime_config: Path
    output_root: Path

    @classmethod
    def from_root(cls, root: Path) -> "DashboardPaths":
        root = root.resolve()
        return cls(
            root,
            root / "config" / "portfolio_p_v1.json",
            root / "data" / "dashboard_v1" / "state.json",
            root / "data" / "dashboard_v1" / "runtime_portfolio.json",
            root / "outputs" / "dashboard_v1" / "comparisons",
        )
```

Use these exact result records:

```python
@dataclass(frozen=True)
class DashboardDataStatus:
    available: bool
    retrieved_at: datetime | None
    first_month: date | None
    last_month: date | None
    vintage_id: str | None
    pointer_path: Path
    reason: str | None


@dataclass(frozen=True)
class DashboardReport:
    comparison_id: str
    output_dir: Path
    comparison: HistoricalComparison
```

`current_data_status(paths, state)` materializes the runtime config, loads the current manifest locally, replaces the default component tuple in memory with `state.components`, and obtains first/last shared months through `portfolio_rows()`. It returns retrieval timestamp, current-portfolio coverage and vintage ID. Missing data returns a status with `available=False` and a user-facing reason. A valid catalog change that makes the previous manifest's config hash stale returns `available=False` with `Aggiorna i dati per includere il nuovo catalogo`; malformed file hashes or schemas still raise and are not labelled as merely absent.

- [ ] **Step 4: Implement explicit refresh and immutable comparison publication**

`refresh_dashboard_data(paths, state, *, downloader=None, retrieved_at=None)` materializes the runtime config, then calls:

```python
refresh_portfolio_prices(
    paths.runtime_config,
    project_root=paths.project_root,
    downloader=downloader,
    retrieved_at=retrieved_at,
)
```

Propagate errors unchanged so Streamlit can display the failed ticker/cause. Do not catch an error by replacing the current pointer.

`publish_dashboard_comparison(paths, state, study_id)` must:

1. load the matching validated vintage locally;
2. translate `state.components` directly into the reference component tuple;
3. select the target from the union of default components and catalog, converting it into `StudySpec`; default-component source URLs use `https://finance.yahoo.com/quote/<ticker>` and catalog entries retain their confirmed URL;
4. call `compare_single_etf()`;
5. serialize monthly, summary, and contribution CSV bytes;
6. compute `comparison_id` from runtime config hash, vintage ID, `state_sha256(state)`, and target ID;
7. publish through `_atomic_snapshot()` to `paths.output_root / comparison_id`.

The monthly CSV has exact columns:

```text
month,etf_return,portfolio_return,etf_cumulative_value,portfolio_cumulative_value,etf_drawdown,portfolio_drawdown,etf_trailing_volatility_12m,portfolio_trailing_volatility_12m,rolling_correlation_12m
```

The contribution CSV has `month`, one monthly column and one `_cumulative` column per current component ID, and `portfolio_return`. Each monthly row reconciles to `portfolio_return`; each cumulative row reconciles to `portfolio_cumulative_value / 100 - 1`. The summary CSV contains all `HistoricalComparisonSummary` fields, including month-win counts, plus `study_id,name,ticker,isin,cumulative_return_difference` and `SHORT_LIVE_HISTORY`.

- [ ] **Step 5: Run the service suite and existing monitor suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard_service.py tests/test_portfolio_monitor.py -v
```

Expected: all tests PASS; no real network request occurs.

- [ ] **Step 6: Commit Task 4 only**

```powershell
git add perpetual_engine/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat: publish dashboard historical comparisons"
```

---

### Task 5: Four-section Streamlit interface

**Files:**
- Create: `perpetual_engine/dashboard.py`
- Create: `tests/test_dashboard.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: every public dashboard-service interface from Tasks 2-4.
- Produces: `main(paths: DashboardPaths | None = None) -> None` and the Streamlit entry point.
- Test root: optional `ETF_DASHBOARD_ROOT` environment variable, used only to point AppTest at a temporary project; normal launch uses the repository root.

- [ ] **Step 1: Pin Streamlit and install the approved dependency**

Append exactly:

```text
streamlit==1.63.0
```

Run:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: Streamlit 1.63.0 installs successfully. Network approval may be required at execution time.

- [ ] **Step 2: Write failing headless UI tests**

Copy the default config into a temporary root, set `ETF_DASHBOARD_ROOT`, and use the official AppTest API:

```python
def test_startup_renders_saved_data_without_network(self):
    import os
    from pathlib import Path
    from unittest.mock import patch

    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).parents[1] / "perpetual_engine" / "dashboard.py"
    with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
        "perpetual_engine.dashboard.refresh_dashboard_data", side_effect=AssertionError("network refresh at startup")
    ), patch("perpetual_engine.dashboard.search_etfs", side_effect=AssertionError("ETF search at startup")):
        app = AppTest.from_file(app_path, default_timeout=10).run()

    self.assertFalse(app.exception)
    self.assertEqual(app.title[0].value, "Analisi ETF")
    self.assertEqual(app.sidebar.radio(key="section").options, ["Portafoglio", "ETF", "Confronti", "Previsioni Chronos"])
```

Add one test per sidebar selection. Assert that Portafoglio contains `Salva portafoglio` and `Ripristina portafoglio predefinito`, ETF contains query and confirmation controls, Confronti contains target/mode selectors, and Chronos displays `Non ancora attivo` without importing or invoking a Chronos model.

- [ ] **Step 3: Run AppTest and verify the entry point is absent**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v
```

Expected: FAIL because `perpetual_engine/dashboard.py` does not exist.

- [ ] **Step 4: Implement the shell and offline startup**

Resolve paths once and render Italian copy:

```python
def _paths() -> DashboardPaths:
    configured = os.environ.get("ETF_DASHBOARD_ROOT")
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent.parent
    return DashboardPaths.from_root(root)


def main(paths: DashboardPaths | None = None) -> None:
    paths = paths or _paths()
    st.set_page_config(page_title="Analisi ETF", layout="wide")
    st.title("Analisi ETF")
    state = load_dashboard_state(paths.default_config, paths.state)
    status = current_data_status(paths, state)
    section = st.sidebar.radio(
        "Sezione",
        ("Portafoglio", "ETF", "Confronti", "Previsioni Chronos"),
        key="section",
    )
```

Show last successful update, common period, and short-history warning from `status`. Catch state corruption separately and display the error plus a reset button; never overwrite the bad file during normal startup.

- [ ] **Step 5: Implement Portafoglio and explicit data refresh**

Keep a draft holding list in `st.session_state`. Render its identity columns as disabled and only `Peso %` as editable with `st.data_editor(..., num_rows="fixed")`. Provide `Aggiungi dal catalogo` and `Rimuovi dal portafoglio` selectors beside the editor; adding creates a provisional 1% row and removal never changes the saved state until confirmation. Convert displayed percentages to fractions, build a candidate `DashboardState`, and call `save_dashboard_state()` only from `Salva portafoglio`. Display validation failures without altering the saved state.

Use a two-step reset: `Ripristina portafoglio predefinito` sets a session flag, then `Conferma ripristino` calls `reset_dashboard_state()` and reruns.

Place `Aggiorna dati` in the sidebar. Only its click handler calls `refresh_dashboard_data()` inside `st.status("Aggiornamento dati...")`. Disable state-changing controls while `st.session_state["refreshing"]` is true; clear the flag in `finally`, then rerun after success.

- [ ] **Step 6: Implement ETF, Confronti, and inactive Chronos sections**

ETF uses a form containing one `Simbolo o ISIN` text input. `Cerca` calls `search_etfs()` and stores the returned candidates in session state. Display a selectbox with `name — ticker — ISIN — exchange`; `Conferma ETF` calls `add_catalog_entry(state, candidate)` followed by `save_dashboard_state()`. Removal calls `remove_catalog_entry()` and reports a held-ETF refusal.

Confronti selects any default component or catalog entry. Keep `Confronta con il portafoglio principale` as the default mode and expose `Portafoglio singolo (100%)` as the study label without changing saved holdings. On `Calcola confronto`, call `publish_dashboard_comparison()` and render:

- metrics from `comparison_summary.csv`;
- a 100-euro growth line chart;
- a 12-month volatility line chart;
- a drawdown line chart;
- monthly returns and component-contribution tables;
- three `st.download_button()` controls for the generated CSV files.

Chronos performs no import or model call and renders only explanatory text, including:

```python
st.info("Non ancora attivo: verrà aggiunto dopo la verifica delle analisi storiche.")
st.write("La fase successiva valuterà tasso BCE, Treasury USA decennale, inflazione USA, petrolio e liquidità mondiale, misurando il contributo predittivo di ogni serie.")
```

Use one `_show_error(error)` helper for every action: show a short Italian `st.error()` and place `repr(error)` inside `st.expander("Dettagli tecnici")`.

- [ ] **Step 7: Run UI and service tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/test_dashboard_service.py -v
```

Expected: all tests PASS, `app.exception` is empty for all four sections, and startup network sentinels are untouched.

- [ ] **Step 8: Commit Task 5 only**

```powershell
git add requirements.txt perpetual_engine/dashboard.py tests/test_dashboard.py
git commit -m "feat: add local ETF dashboard"
```

---

### Task 6: Desktop launch, documentation, and end-to-end verification

**Files:**
- Create: `Avvia Analisi ETF.cmd`
- Create: `scripts/install_dashboard_shortcut.ps1`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: `.venv\Scripts\python.exe`, `perpetual_engine/dashboard.py`.
- Produces: a desktop shortcut named `Analisi ETF.lnk` whose target is the repository launcher.

- [ ] **Step 1: Add the Windows launcher**

Create `Avvia Analisi ETF.cmd` with exactly this control flow:

```bat
@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente del programma non trovato. Eseguire prima l'installazione.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run "perpetual_engine\dashboard.py" --server.address=127.0.0.1 --server.port=8501 --server.headless=false --browser.gatherUsageStats=false
if errorlevel 1 (
  echo Impossibile avviare Analisi ETF. Verificare che la porta 8501 sia libera.
  pause
)
```

This keeps startup local and leaves a readable message when the environment is missing or port 8501 cannot be used.

- [ ] **Step 2: Add the idempotent desktop-shortcut installer**

Create `scripts/install_dashboard_shortcut.ps1`:

```powershell
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $projectRoot "Avvia Analisi ETF.cmd"
if (-not (Test-Path -LiteralPath $launcher)) { throw "Lanciatore non trovato: $launcher" }
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Analisi ETF.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
$shortcut.Save()
Write-Host "Collegamento creato: $shortcutPath"
```

Running it twice updates the same shortcut and creates no duplicate.

- [ ] **Step 3: Ignore only mutable dashboard artifacts and document the workflow**

Append to `.gitignore`:

```text
data/dashboard_v1/
outputs/dashboard_v1/
```

Add a README section `App locale Analisi ETF` with these exact user actions: run the shortcut installer once, double-click `Analisi ETF`, inspect cached data, press `Aggiorna dati` when desired, add by symbol/ISIN, edit weights, restore 60/15/15/10, calculate a historical comparison, download CSV, and close the launcher window to stop the local app. State that Chronos is not active in v1.

- [ ] **Step 4: Run static and dependency checks**

Run:

```powershell
.venv\Scripts\python.exe -m compileall -q perpetual_engine
.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: all commands exit 0; `pip check` prints `No broken requirements found.`

- [ ] **Step 5: Run the complete automated suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: every existing and new test PASS with no network requirement.

- [ ] **Step 6: Smoke-test the localhost binding**

Start Streamlit hidden for the smoke check, poll only localhost for at most 15 seconds, and always stop the exact process started by the test:

```powershell
$dashboardProcess = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList @("-m","streamlit","run","perpetual_engine\dashboard.py","--server.address=127.0.0.1","--server.port=8501","--server.headless=true","--browser.gatherUsageStats=false") -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru
try {
  $ready = $false
  1..15 | ForEach-Object {
    if (-not $ready) {
      try { $ready = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 1).StatusCode -eq 200 } catch { Start-Sleep -Seconds 1 }
    }
  }
  if (-not $ready) { throw "Dashboard locale non raggiungibile" }
} finally {
  if (-not $dashboardProcess.HasExited) { Stop-Process -Id $dashboardProcess.Id }
}
```

Expected: the health endpoint returns 200 and the process binds only to `127.0.0.1:8501`.

- [ ] **Step 7: Install and manually verify the desktop shortcut**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_dashboard_shortcut.ps1
```

Then double-click `Analisi ETF` and verify this sequence without a terminal command: cached status appears; a valid weight edit persists after reopening; reset restores 60/15/15/10; GRID is selectable; explicit refresh succeeds; GRID comparison renders metrics, growth, volatility, drawdown and contributions; each CSV downloads; Chronos remains inactive.

- [ ] **Step 8: Commit Task 6 only and record the final checkpoint**

```powershell
git add ".gitignore" "README.md" "Avvia Analisi ETF.cmd" "scripts/install_dashboard_shortcut.ps1"
git commit -m "docs: add ETF dashboard desktop launcher"
git status --short
git log -1 --oneline
```

Expected: the commit contains only launcher/documentation paths. Pre-existing unrelated output directories remain untracked or ignored and are not committed.
