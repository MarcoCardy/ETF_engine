# Chronos-2 Market Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, reproducible Chronos-2 workflow that forecasts the four monthly return series under three ECB-rate scenarios, measures the predictive contribution of ECB, US Treasury 10Y, Brent, BIS liquidity, and US inflation data, tracks every issued forecast against later realized returns, and monitors the explicitly configured ETF portfolio P.

**Architecture:** Add one data module for strict configuration, source parsing, monthly alignment, frozen vintages, and target loading; add one forecasting module for Chronos inference, walk-forward evaluation, attribution, immutable publication, reconciliation, and small derived diagnostics. Extend the existing CLI with a `chronos` group and reuse the project's canonical JSON, SHA-256, FRED parser, Treasury availability rule, sample-volatility calculation, portfolio arithmetic, and atomic directory-publication pattern.

**Tech Stack:** Python 3.12, standard-library `csv`/`json`/`zipfile`/`tempfile`, NumPy, PyTorch CPU, `chronos-forecasting==2.3.1`, `transformers>=4.41,<5`, and `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-03-chronos-2-market-forecast-design.md`

## Global Constraints

- Load only `amazon/chronos-2` revision `29ec3766d36d6f73f0696f85560a422f50e8498c` on CPU with `local_files_only=True`.
- Forecast exactly 12 monthly periods and emit quantiles `0.10`, `0.50`, and `0.90`.
- Freeze the v1 rate movement at 100 basis points so `ECB_DOWN_100BP` and `ECB_UP_100BP` remain truthful identifiers.
- Targets are ordered `WORLD`, `MOMENTUM`, `QUALITY`, `TREND` and are read only from the manifest-validated four-sleeve artifact.
- Covariates are ordered `ECB_DFR`, `US_TREASURY_10Y`, `BRENT_RETURN`, `BIS_USD_CREDIT_YOY`, `US_CPI_YOY`.
- Preserve `US_TREASURY_10Y` in percentage points: `4.25` means 4.25%, not `0.0425`.
- Only `ECB_DFR` may appear in `future_covariates`; Treasury, Brent, BIS, and CPI remain historical-only.
- `refresh` and `portfolio-refresh` are the only networked commands. All other commands must run offline and fail closed on hash mismatch.
- Publish vintages, forecasts, evaluations, and monitoring snapshots atomically; never overwrite an immutable artifact.
- Label forecasts as research only; no command may alter allocations, portfolio weights, or place orders.
- The directory is currently not a Git repository. Do not initialize Git automatically; execute commit steps only if the user has created a repository before implementation.

## File Structure

- Create `config/chronos_v1.json`: frozen model, target, source, scenario, evaluation, and bootstrap settings.
- Create `perpetual_engine/chronos_data.py`: configuration types, target validation, ECB/BIS parsing, monthly transforms, frozen refresh transaction, and vintage loading.
- Create `perpetual_engine/chronos.py`: scenario construction, Chronos adapter, forecast/evaluation metrics, covariate attribution, immutable publication, and reconciliation.
- Modify `perpetual_engine/cli.py`: parse and dispatch the Chronos forecast and portfolio commands while preserving existing commands and exit-code behavior.
- Create `tests/test_chronos.py`: one focused offline test module with tiny deterministic fixtures and a fake predictor.
- Create `config/portfolio_p_v1.json`, `perpetual_engine/portfolio_monitor.py`, and `tests/test_portfolio_monitor.py` for the separate realized portfolio monitor.

---

### Task 1: Configuration and target-data contract

**Files:**
- Create: `config/chronos_v1.json`
- Create: `perpetual_engine/chronos_data.py`
- Create: `tests/test_chronos.py`

**Interfaces:**
- Produces: `ChronosConfig`, `MonthlyTable`, `load_chronos_config(path: Path) -> ChronosConfig`, and `load_target_table(config: ChronosConfig) -> MonthlyTable`.
- Consumes: `canonical_json()` and `sha256_file()` from `perpetual_engine.io`.

- [ ] **Step 1: Write failing tests for the fixed configuration and target manifest**

Add a `ChronosConfigTests(unittest.TestCase)` class. Its fixture writes a three-month CSV with the exact target columns and a manifest whose `generated_sha256["monthly_returns.csv"]` equals the fixture hash. Assert the frozen constants, target order, month order, `(4, 3)` value shape, and decimal-return values. Add separate assertions that a bad generated hash, duplicate month, missing month, non-month-end date, non-finite value, or reordered required column raises `ValueError`.

```python
def test_load_target_table_validates_manifest_and_order(self):
    config = load_chronos_config(self.config_path)
    table = load_target_table(config)
    self.assertEqual(table.names, ("WORLD", "MOMENTUM", "QUALITY", "TREND"))
    self.assertEqual(table.values.shape, (4, 3))
    self.assertEqual(table.months[-1], date(2024, 3, 31))
    self.assertAlmostEqual(float(table.values[0, 0]), 0.01)

def test_load_target_table_rejects_manifest_hash_mismatch(self):
    manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
    manifest["generated_sha256"]["monthly_returns.csv"] = "0" * 64
    self.manifest_path.write_bytes(canonical_json(manifest))
    with self.assertRaisesRegex(ValueError, "target hash"):
        load_target_table(load_chronos_config(self.config_path))
```

- [ ] **Step 2: Run the focused tests and confirm the expected import failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v`

Expected: `ERROR` because `perpetual_engine.chronos_data` does not exist.

- [ ] **Step 3: Add the exact JSON configuration**

Write `config/chronos_v1.json` with these values:

```json
{
  "schema_version": "CHRONOS_CONFIG_V1",
  "model": {
    "id": "amazon/chronos-2",
    "revision": "29ec3766d36d6f73f0696f85560a422f50e8498c",
    "device": "cpu"
  },
  "target": {
    "csv": "outputs/four_sleeve_v1/monthly_returns.csv",
    "manifest": "outputs/four_sleeve_v1/manifest.json",
    "columns": ["WORLD", "MOMENTUM", "QUALITY", "TREND"]
  },
  "prediction_length": 12,
  "quantiles": [0.1, 0.5, 0.9],
  "scenario_basis_points": 100,
  "data_root": "data/chronos_v1",
  "sources": [
    {"id": "ECB_DFR", "url": "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata", "parser": "ecb_dfr", "role": "known_future"},
    {"id": "US_TREASURY_10Y", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", "parser": "fred_dgs10", "role": "past_only"},
    {"id": "BRENT_RETURN", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU", "parser": "fred_brent", "role": "past_only"},
    {"id": "BIS_USD_CREDIT_YOY", "url": "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip", "parser": "bis_gli", "role": "past_only"},
    {"id": "US_CPI_YOY", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS", "parser": "fred_cpi", "role": "past_only"}
  ],
  "evaluation": {"origins": 36, "reported_horizons": [1, 3, 6, 12]},
  "bootstrap": {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95}
}
```

- [ ] **Step 4: Implement strict configuration and target loading**

Use frozen dataclasses and NumPy arrays. Resolve every configured path relative to the project root (`config_path.resolve().parent.parent`), require it to remain below that root with `Path.is_relative_to`, accept only the exact model/device/prediction/quantile values and `scenario_basis_points == 100`, and require exactly the five ordered v1 source IDs once each. Adding a later series requires an explicit parser and normalizer change before configuration accepts it.

```python
@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    url: str
    parser: str
    role: str

@dataclass(frozen=True)
class ChronosConfig:
    path: Path
    project_root: Path
    model_id: str
    model_revision: str
    device: str
    target_csv: Path
    target_manifest: Path
    targets: tuple[str, ...]
    prediction_length: int
    quantiles: tuple[float, ...]
    scenario_basis_points: int
    data_root: Path
    sources: tuple[SourceSpec, ...]
    evaluation_origins: int
    reported_horizons: tuple[int, ...]
    bootstrap_block_months: int
    bootstrap_resamples: int
    bootstrap_seed: int
    bootstrap_confidence: float
    config_hash: str

@dataclass(frozen=True)
class MonthlyTable:
    months: tuple[date, ...]
    names: tuple[str, ...]
    values: np.ndarray
```

`load_target_table()` must require the CSV header to start with `month,WORLD,MOMENTUM,QUALITY,TREND`, permit later strategy columns only after those targets, validate the manifest schema and generated hash, parse finite floats, and verify unique contiguous month ends. Set `values.flags.writeable = False` before returning.

- [ ] **Step 5: Run the target tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v`

Expected: all `ChronosConfigTests` pass.

- [ ] **Step 6: Record the checkpoint**

Run: `git rev-parse --is-inside-work-tree`

If it returns `true`, run `git add config/chronos_v1.json perpetual_engine/chronos_data.py tests/test_chronos.py` followed by `git commit -m "feat: define Chronos data contract"`. If it fails because the project is not a repository, do not initialize Git and continue.

---

### Task 2: Covariate parsing and point-in-time monthly alignment

**Files:**
- Modify: `config/chronos_v1.json`
- Modify: `perpetual_engine/chronos_data.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Consumes: `SourceArtifact`, `parse_fred_csv()`, and `treasury_available_at()` from `perpetual_engine.data_sources`.
- Produces: `parse_ecb_dfr(artifact: SourceArtifact) -> tuple[ObservationRow, ...]`, `parse_bis_gli(artifact: SourceArtifact) -> tuple[ObservationRow, ...]`, the internal FRED wrapper for `DGS10`, `DCOILBRENTEU`, and `CPIAUCNS`, and `normalize_covariates(config: ChronosConfig, target_months: tuple[date, ...], rows_by_id: Mapping[str, tuple[ObservationRow, ...]]) -> MonthlyTable`.

- [ ] **Step 1: Write failing parser and availability-boundary tests**

Create tiny raw fixtures for all five sources. Assert:

- ECB uses the last effective rate on or before each month end;
- DGS10 values `4.20` and `4.30` produce a monthly mean of `4.25`, never `0.0425`;
- a DGS10 month-end observation is excluded when its `treasury_available_at()` is after the monthly cut-off;
- Brent uses `log(mean_t / mean_t_minus_1)` and excludes observations until seven calendar days after observation;
- BIS accepts only `Q.USD.3P.N.A.I.B.771`, marks the quarter available four months after quarter end, then carries the released value monthly without interpolation;
- CPI accepts only `CPIAUCNS`, computes headline year-on-year percent from the non-seasonally-adjusted index, and excludes a month until the end of the following calendar month;
- missing months, duplicate observation dates within any source even across different artifact hashes, malformed numbers, an unexpected BIS key, or a non-finite normalized value raise `ValueError`;
- v1 configuration accepts exactly the five required source IDs in their frozen order; later sources require explicit parser, normalizer, and validation support before configuration accepts them.

```python
def test_dgs10_stays_in_published_percentage_points(self):
    rows = self.fred_rows("DGS10", (("2024-01-02", "4.20"), ("2024-01-03", "4.30")), source_scale="raw")
    table = normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(dgs10=rows))
    column = table.names.index("US_TREASURY_10Y")
    self.assertAlmostEqual(float(table.values[column, 0]), 4.25)

def test_bis_release_is_not_carried_before_four_month_lag(self):
    rows = parse_bis_gli(self.bis_artifact("2023-Q4", "5.6"))
    with self.assertRaisesRegex(ValueError, "BIS.*unavailable"):
        normalize_covariates(self.config, (date(2024, 3, 31),), self.rows_with(bis=rows))
```

- [ ] **Step 2: Run the covariate tests and verify the new functions are absent**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v`

Expected: `ERROR` naming `parse_ecb_dfr`, `parse_bis_gli`, or `normalize_covariates`.

- [ ] **Step 3: Implement ECB and BIS parsers with standard-library readers**

For ECB, require the exact configured series key in `KEY`, plus unique `TIME_PERIOD` and finite `OBS_VALUE`. Set `available_at` to the effective date at 00:00 UTC and preserve percentage points.

For BIS, open the single member `WS_GLI_csv_flat.csv` with `zipfile.ZipFile`, require these headers exactly once: `FREQ:Frequency`, `CURR_DENOM:Currency of denomination`, `BORROWERS_CTY:Borrowers' country`, `BORROWERS_SECTOR:Borrowers' sector`, `LENDERS_SECTOR:Lending sector`, `L_POS_TYPE:Position type`, `L_INSTR:Type of instruments`, `UNIT_MEASURE:Unit of measure`, `TIME_PERIOD:Time period or range`, `OBS_VALUE:Observation Value`, and `TITLE:Title`. Select only the dimensions `Q,USD,3P,N,A,I,B` and unit code `771`; require quarterly periods in `YYYY-Qn` form and finite percentage values.

```python
def _month_end(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return following - timedelta(days=1)

def _quarter_end(period: str) -> date:
    year_text, quarter_text = period.split("-Q")
    month = int(quarter_text) * 3
    return _month_end(date(int(year_text), month, 1))

def _bis_available_at(period_end: date) -> datetime:
    month_index = period_end.year * 12 + period_end.month - 1 + 4
    year, month_zero = divmod(month_index, 12)
    available = _month_end(date(year, month_zero + 1, 1))
    return datetime(available.year, available.month, available.day, 23, 59, 59, tzinfo=timezone.utc)
```

- [ ] **Step 4: Implement monthly point-in-time transformations**

Build every monthly value using only rows with `available_at <= month_end 23:59:59 UTC`. Call the existing FRED parser for DGS10 with `series_id="DGS10"`, `source_scale="raw"`, `unit="percent_per_annum"`, `frequency="daily"`, and `availability="treasury_next_business_day"`. Call it for Brent with `source_scale="raw"` and replace each parsed row's availability with observation date plus seven calendar days before computing monthly means and log returns. Call it for `CPIAUCNS` as a raw monthly NSA index, set availability to the end of the following month, and compute exact 12-month headline inflation. Do not forward-fill Treasury, Brent, or CPI. Forward-fill only ECB effective rates and already-released BIS quarters.

```python
REQUIRED_V1_COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")

def covariate_names(config: ChronosConfig) -> tuple[str, ...]:
    return tuple(source.source_id for source in config.sources)

def _eligible(rows: Iterable[ObservationRow], cutoff: datetime) -> tuple[ObservationRow, ...]:
    return tuple(row for row in validate_rows(rows) if row.available_at <= cutoff)

def _daily_month_mean(rows: Iterable[ObservationRow], month: date, cutoff: datetime) -> float:
    values = [float(row.value) for row in _eligible(rows, cutoff) if _month_end(row.observation_date) == month]
    if not values:
        raise ValueError(f"daily series is unavailable for {month.isoformat()}")
    return math.fsum(values) / len(values)
```

- [ ] **Step 5: Run the covariate tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCovariateTests -v`

Expected: all `ChronosCovariateTests` pass.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add config/chronos_v1.json perpetual_engine/chronos_data.py tests/test_chronos.py` and `git commit -m "feat: normalize Chronos covariates"`. Otherwise continue without initializing Git.

---

### Task 3: Frozen covariate refresh transaction

**Files:**
- Modify: `perpetual_engine/chronos_data.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Consumes: `fetch_url()`, `freeze_bytes()`, `canonical_json()`, `sha256_file()`, and the parsers from Task 2.
- Produces: `refresh_chronos_data(config_path: Path, *, fetcher: Callable[[str], bytes] = fetch_url, retrieved_at: datetime | None = None) -> str` and `load_covariate_table(config: ChronosConfig) -> tuple[MonthlyTable, str]`.

- [ ] **Step 1: Write failing transactional-refresh tests**

Use a temporary project and an injected dictionary-backed `fetcher`. Assert that a successful refresh creates:

```text
data/chronos_v1/vintages/<vintage_id>/raw/
data/chronos_v1/vintages/<vintage_id>/covariates.csv
data/chronos_v1/vintages/<vintage_id>/manifest.json
data/chronos_v1/current_manifest.json
```

Assert that every raw and normalized hash reconciles, a repeated identical refresh returns the same vintage ID, and `load_covariate_table()` returns the expected ordered matrix. Inject one malformed source and assert the old pointer bytes and old vintage remain unchanged with no partial published vintage.

```python
def test_refresh_is_atomic_when_one_source_fails(self):
    first_id = refresh_chronos_data(self.config_path, fetcher=self.good_fetcher, retrieved_at=self.at)
    pointer = self.data_root / "current_manifest.json"
    original = pointer.read_bytes()
    with self.assertRaises(ValueError):
        refresh_chronos_data(self.config_path, fetcher=self.fetcher_with_bad_bis, retrieved_at=self.at)
    self.assertEqual(pointer.read_bytes(), original)
    self.assertTrue((self.data_root / "vintages" / first_id).is_dir())
```

- [ ] **Step 2: Run the refresh tests and verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosRefreshTests -v`

Expected: `ERROR` because `refresh_chronos_data` and `load_covariate_table` are absent.

- [ ] **Step 3: Implement staging, deterministic identity, and pointer replacement**

Fetch each configured URL exactly once, freeze it under the staging directory, parse all rows, normalize to target months, and serialize CSV with `\n` line endings and `format(value, ".17g")`. Define the vintage ID as SHA-256 of canonical JSON containing the configuration hash, ordered source URLs and raw hashes, exact availability-rule names, and normalized CSV hash. Publish with `Path.replace()` only after every validation and hash succeeds.

```python
identity = {
    "config_hash": config.config_hash,
    "sources": [{"id": spec.source_id, "url": spec.url, "sha256": raw_hashes[spec.source_id]} for spec in config.sources],
    "availability": {"ECB_DFR": "effective_date", "US_TREASURY_10Y": "treasury_next_business_day", "BRENT_RETURN": "observation_plus_7_days", "BIS_USD_CREDIT_YOY": "quarter_end_plus_4_months", "US_CPI_YOY": "following_month_end"},
    "covariates_sha256": hashlib.sha256(covariate_bytes).hexdigest(),
}
vintage_id = hashlib.sha256(canonical_json(identity)).hexdigest()
```

If the immutable destination exists, verify its manifest and file hashes and reuse it; otherwise replace the staged directory into place. Write the current manifest to a same-directory temporary file and replace the pointer last. In `finally`, remove only the verified staging child created by `tempfile.mkdtemp()`.

- [ ] **Step 4: Implement strict offline vintage loading**

`load_covariate_table()` must resolve the manifest-selected vintage under `config.data_root / "vintages"`, reject path escape, validate the manifest schema/config hash/vintage ID, verify every recorded raw and generated hash, then parse `month` followed by the configured source IDs into an immutable `MonthlyTable`. For `config/chronos_v1.json`, the required header is exactly `month,ECB_DFR,US_TREASURY_10Y,BRENT_RETURN,BIS_USD_CREDIT_YOY,US_CPI_YOY`.

- [ ] **Step 5: Run the refresh tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosRefreshTests -v`

Expected: all `ChronosRefreshTests` pass.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add perpetual_engine/chronos_data.py tests/test_chronos.py` and `git commit -m "feat: publish frozen Chronos vintages"`. Otherwise continue without initializing Git.

---

### Task 4: ECB scenarios and the offline Chronos adapter

**Files:**
- Create: `perpetual_engine/chronos.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Consumes: `ChronosConfig`, `MonthlyTable`, `load_target_table()`, and `load_covariate_table()`.
- Produces: `ForecastRow`, `ecb_scenarios(last_rate: float, basis_points: int, length: int) -> dict[str, np.ndarray]`, `build_forecast_inputs(targets: MonthlyTable, covariates: MonthlyTable, scenarios: Mapping[str, np.ndarray]) -> list[dict[str, object]]`, `load_chronos_predictor(config: ChronosConfig) -> Callable`, and `run_scenario_forecasts(config: ChronosConfig, predictor: Callable | None = None) -> tuple[ForecastRow, ...]`.

- [ ] **Step 1: Write failing scenario, input-shape, and fake-inference tests**

Assert the exact 12-step paths from `r0=3.0`: flat stays `3.0`, down ends at `2.0`, and up ends at `4.0`. Verify three inputs each contain a `(4, history)` target, five equally sized past-covariate arrays, and future covariates containing only a 12-value `ECB_DFR`. The fake predictor returns `(4, 12, 3)` quantiles; assert 144 forecast rows, ordered by scenario/target/horizon, with finite ordered `q10 <= q50 <= q90`.

```python
def fake_predictor(items, prediction_length, quantile_levels):
    self.assertEqual(prediction_length, 12)
    self.assertEqual(quantile_levels, [0.1, 0.5, 0.9])
    return [np.stack((np.full((4, 12), -0.01), np.zeros((4, 12)), np.full((4, 12), 0.01)), axis=-1) for _ in items]
```

- [ ] **Step 2: Run the forecasting-unit tests and verify the missing module**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosForecastTests -v`

Expected: `ERROR` because `perpetual_engine.chronos` does not exist.

- [ ] **Step 3: Implement scenario construction and model-independent input preparation**

```python
SCENARIO_NAMES = ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP")

def ecb_scenarios(last_rate: float, basis_points: int, length: int) -> dict[str, np.ndarray]:
    movement = basis_points / 100.0
    steps = np.arange(1, length + 1, dtype=np.float32) / length
    return {
        "ECB_FLAT": np.full(length, last_rate, dtype=np.float32),
        "ECB_DOWN_100BP": last_rate - movement * steps,
        "ECB_UP_100BP": last_rate + movement * steps,
    }
```

`build_forecast_inputs()` must align the two monthly tables exactly, truncate both to the latest common month, validate finite values, and preserve configured ordering. Convert to `float32` only at the Chronos boundary.

```python
@dataclass(frozen=True)
class ForecastRow:
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float
```

- [ ] **Step 4: Implement the lazy-loaded offline model adapter**

Import Chronos only inside `load_chronos_predictor()` so parser and metric tests do not load PyTorch. Load once per command and convert returned tensors to NumPy.

```python
def load_chronos_predictor(config: ChronosConfig):
    from chronos import Chronos2Pipeline
    pipeline = Chronos2Pipeline.from_pretrained(
        config.model_id,
        revision=config.model_revision,
        device_map="cpu",
        local_files_only=True,
    )
    def predict(items, prediction_length, quantile_levels):
        quantiles, _ = pipeline.predict_quantiles(
            items,
            prediction_length=prediction_length,
            quantile_levels=list(quantile_levels),
            batch_size=len(items),
        )
        return [tensor.detach().cpu().numpy() for tensor in quantiles]
    return predict
```

Validate count, `(4, 12, 3)` shape, finiteness, and quantile ordering before constructing `ForecastRow` values.

- [ ] **Step 5: Run the forecasting-unit tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosForecastTests -v`

Expected: all `ChronosForecastTests` pass without loading the real model.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add perpetual_engine/chronos.py tests/test_chronos.py` and `git commit -m "feat: add Chronos scenario forecasting"`. Otherwise continue without initializing Git.

---

### Task 5: Walk-forward metrics and covariate contribution

**Files:**
- Modify: `perpetual_engine/chronos.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Produces: `pinball_loss(actual: np.ndarray, forecast: np.ndarray, quantile: float) -> np.ndarray`, `moving_block_interval(origin_differences: np.ndarray, config: ChronosConfig) -> tuple[float, float]`, `evaluation_variants(covariates: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]`, and `evaluate_chronos(config_path: Path, output: Path, predictor: Callable | None = None) -> Path`.
- Consumes: the frozen tables and forecast-input functions from Tasks 1–4.

- [ ] **Step 1: Write failing metric, origin, batching, and classification tests**

Assert:

- 36 origins run from `2022-06-30` through `2025-05-31` for the frozen 240-month target;
- every origin uses expanding history only and has all 12 realized months;
- five covariates produce exactly twelve Chronos variants: target-only, five standalone additions, full, and five leave-one-out variants;
- the zero-return comparator and `ORACLE_FUTURE_RATE_UPPER_BOUND` are separate from those twelve;
- MAE, three-quantile mean pinball loss, and 80% coverage match hand-calculated arrays at horizons 1, 3, 6, 12 and pooled `ALL`;
- paired bootstrap is identical across repeated seed-42 runs;
- positive, negative, and zero-crossing intervals classify `USEFUL`, `HARMFUL`, and `INCONCLUSIVE`.

```python
def test_pinball_loss(self):
    actual = np.array([1.0, -1.0])
    forecast = np.array([0.0, 0.0])
    np.testing.assert_allclose(pinball_loss(actual, forecast, 0.1), np.array([0.1, 0.9]))

def test_five_covariates_make_twelve_chronos_variants(self):
    covariates = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")
    variants = evaluation_variants(covariates)
    self.assertEqual(len(variants), 12)
    self.assertIn(("FULL_MINUS_US_CPI_YOY", covariates[:-1]), variants)
```

- [ ] **Step 2: Run the evaluation tests and verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosEvaluationTests -v`

Expected: `ERROR` naming the missing metric and evaluation functions.

- [ ] **Step 3: Implement metrics, bounded variant generation, and bootstrap**

```python
def pinball_loss(actual: np.ndarray, forecast: np.ndarray, quantile: float) -> np.ndarray:
    error = actual - forecast
    return np.maximum(quantile * error, (quantile - 1.0) * error)

def moving_block_interval(origin_differences: np.ndarray, config: ChronosConfig) -> tuple[float, float]:
    values = np.asarray(origin_differences, dtype=float)
    block = config.bootstrap_block_months
    if values.ndim != 1 or len(values) < block or not np.isfinite(values).all():
        raise ValueError("bootstrap requires finite per-origin differences and one full block")
    rng = np.random.default_rng(config.bootstrap_seed)
    starts = np.arange(len(values) - block + 1)
    means = np.empty(config.bootstrap_resamples)
    blocks_needed = math.ceil(len(values) / block)
    for index in range(config.bootstrap_resamples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([values[start:start + block] for start in chosen])[:len(values)]
        means[index] = sample.mean()
    tail = (1.0 - config.bootstrap_confidence) / 2.0
    return float(np.quantile(means, tail)), float(np.quantile(means, 1.0 - tail))
```

Generate variants directly from the configured tuple; do not enumerate all subsets. Classify only the conditional full-minus comparison.

- [ ] **Step 4: Implement batched expanding walk-forward evaluation and atomic output**

Require exactly 36 fully evaluable 12-month origins or raise `ValueError`. Build all 36 items for one variant, call the predictor once for that variant, then release its batch before moving to the next variant to limit CPU memory. Operational variants omit future ECB values. Only the oracle supplies the realized next 12 ECB observations, and its name and manifest label must contain `ORACLE_FUTURE_RATE_UPPER_BOUND`. Reporting classifications must never rewrite the enabled-covariate configuration or automatically select a series.

Write staged files with these columns:

- `predictions.csv`: `variant,origin,target,forecast_month,horizon,actual,q10,q50,q90`;
- `metrics.csv`: `variant,target,horizon,mae_q50,mean_pinball_loss,interval_80_coverage`;
- `covariate_contribution.csv`: `covariate,comparison,horizon,loss_without,loss_with,improvement,ci_low,ci_high,classification`.
- `volatility_diagnostics.csv`: `origin,target,horizon,trailing_volatility_12m,volatility_tercile,q10,q50,q90,interval_width,actual,signed_error,absolute_error,interval_hit` for `FULL` only.

Reuse the existing sample-standard-deviation times `sqrt(12)` convention. Compute low/middle/high thresholds separately by target from the 36 origin-known trailing volatilities, record them in the evaluation manifest, and keep them out of model inputs. Add focused tests for trailing-window boundaries, per-target terciles, interval width, and error columns. Do not add volatility as a sixth covariate in v1.

The evaluation `manifest.json` includes model/package/device fields, configuration/target/vintage hashes, evaluation origins, bootstrap settings, the retrospective research label, and generated-file hashes. Reject an existing output directory unless every byte matches the newly computed immutable output.

- [ ] **Step 5: Run the evaluation tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosEvaluationTests -v`

Expected: all `ChronosEvaluationTests` pass.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add perpetual_engine/chronos.py tests/test_chronos.py` and `git commit -m "feat: evaluate Chronos covariate contribution"`. Otherwise continue without initializing Git.

---

### Task 6: Immutable forecasts and forecast-versus-actual reconciliation

**Files:**
- Modify: `perpetual_engine/chronos.py`
- Modify: `tests/test_chronos.py`

**Interfaces:**
- Produces: `IssuedForecastRow`, `ReconciledRow`, `forecast_id(config: ChronosConfig, target_hash: str, vintage_id: str, origin: date) -> str`, `publish_forecast(config_path: Path, output_root: Path, *, predictor: Callable | None = None, issued_at: datetime | None = None) -> str`, `reconcile_rows(forecasts: Iterable[IssuedForecastRow], actuals: Mapping[date, Mapping[str, float]]) -> tuple[ReconciledRow, ...]`, and `reconcile_forecasts(config_path: Path, forecast_root: Path, output: Path) -> str`.
- Consumes: scenario forecasts, canonical JSON, target/vintage hashes, and `load_target_table()`.

- [ ] **Step 1: Write failing immutable-publication and reconciliation tests**

Using a fake predictor and injected UTC `issued_at`, assert:

- deterministic forecast ID from model revision, configuration hash, target hash, vintage ID, origin, and scenario definitions;
- `forecast.csv` has 144 rows and its manifest records the original issue time and generated hash;
- `scenario_sensitivity.csv` is derived from those same rows without another model call and reports ECB down/up changes in `q50` and `q90-q10` versus flat by target/horizon;
- an identical second call reuses byte-identical output and keeps the first issue time;
- changed bytes under an existing forecast ID fail instead of overwriting;
- reconciliation computes `signed_error = actual - q50`, absolute error, squared error, and inclusive interval hit;
- future months appear only in `pending_forecasts.csv` and never in live metrics;
- metrics aggregate bias, MAE, RMSE, 80% coverage, and count by target/scenario/horizon plus pooled rows;
- adding a realized target month changes `monitoring_id` while preserving the prior snapshot;
- corrupt forecast hashes, ambiguous actual matches, or path escape fail before publication.

```python
def test_reconcile_uses_actual_minus_median(self):
    rows = reconcile_rows(self.forecast_rows(q10=-0.02, q50=0.01, q90=0.04), {date(2024, 2, 29): {"WORLD": 0.03}})
    row = next(item for item in rows if item.target == "WORLD")
    self.assertAlmostEqual(row.signed_error, 0.02)
    self.assertAlmostEqual(row.absolute_error, 0.02)
    self.assertAlmostEqual(row.squared_error, 0.0004)
    self.assertTrue(row.interval_hit)
```

Create `IssuedForecastRow` by combining each validated forecast manifest's identity with its CSV rows, then define the reconciliation output explicitly:

```python
@dataclass(frozen=True)
class IssuedForecastRow:
    forecast_id: str
    issued_at: datetime
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float

@dataclass(frozen=True)
class ReconciledRow:
    forecast_id: str
    issued_at: datetime
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float
    actual: float
    signed_error: float
    absolute_error: float
    squared_error: float
    interval_hit: bool
```

- [ ] **Step 2: Run publication/reconciliation tests and verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosMonitoringTests -v`

Expected: `ERROR` naming the missing publication or reconciliation functions.

- [ ] **Step 3: Implement deterministic forecast identity and one-time publication**

```python
def forecast_id(config: ChronosConfig, target_hash: str, vintage_id: str, origin: date) -> str:
    identity = {
        "model_revision": config.model_revision,
        "config_hash": config.config_hash,
        "target_hash": target_hash,
        "vintage_id": vintage_id,
        "origin": origin.isoformat(),
        "scenarios": {name: values.tolist() for name, values in ecb_scenarios(0.0, config.scenario_basis_points, config.prediction_length).items()},
    }
    return hashlib.sha256(canonical_json(identity)).hexdigest()
```

The scenario identity binds the frozen 100-basis-point paths, while forecast values bind the actual last ECB rate through the covariate-vintage hash. Serialize `forecast.csv` with columns `scenario,target,forecast_month,horizon,q10,q50,q90`. Derive `scenario_sensitivity.csv` with columns `target,forecast_month,horizon,scenario,q50,flat_q50,q50_delta,interval_width,flat_interval_width,interval_width_delta` for down/up versus flat. Its manifest includes forecast ID, first UTC issue time, model/package/device fields, origin, scenario definitions, the retrospective target caveat, configuration/target/vintage hashes, and both CSV hashes. Build all output bytes in a staging child below `output_root / ".staging"`; if the final directory exists, verify every manifest hash and return the original ID without touching `issued_at`. Otherwise write `issued_at` once in UTC, publish with `Path.replace()`, then call `reconcile_forecasts()`.

- [ ] **Step 4: Implement append-only reconciliation snapshots**

Read every direct child of `forecast_root`, require a valid 64-character directory name, validate each manifest and forecast hash, and match `(forecast_month, target)` against the latest manifest-validated target table. Serialize realized and pending rows in stable forecast-ID/scenario/target/horizon order.

```python
signed_error = actual - q50
absolute_error = abs(signed_error)
squared_error = signed_error * signed_error
interval_hit = q10 <= actual <= q90
monitoring_identity = {
    "target_data_hash": target_hash,
    "target_manifest_hash": target_manifest_hash,
    "forecast_manifest_hashes": sorted(forecast_manifest_hashes),
}
monitoring_id = hashlib.sha256(canonical_json(monitoring_identity)).hexdigest()
```

Write these staged files:

- `forecast_vs_actual.csv`: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90,actual,signed_error,absolute_error,squared_error,interval_hit`;
- `pending_forecasts.csv`: `forecast_id,issued_at,origin,scenario,target,forecast_month,horizon,q10,q50,q90`;
- `live_metrics.csv`: `scenario,target,horizon,count,bias,mae,rmse,interval_80_coverage`;
- `manifest.json`: monitoring ID, target-data and target-manifest hashes, ordered forecast-manifest hashes, `PROSPECTIVE_TRACK_RECORD`, and generated-file hashes.

Reuse only a byte-identical existing monitoring snapshot. The pooled metrics must still show `count`; do not emit a metric row for an empty group.

- [ ] **Step 5: Run the monitoring tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosMonitoringTests -v`

Expected: all `ChronosMonitoringTests` pass.

- [ ] **Step 6: Record the checkpoint**

If Git is available, run `git add perpetual_engine/chronos.py tests/test_chronos.py` and `git commit -m "feat: track forecasts against actual returns"`. Otherwise continue without initializing Git.

---

### Task 7: Forecast CLI integration and verification

**Files:**
- Modify: `perpetual_engine/cli.py:22-84`
- Modify: `tests/test_chronos.py`
- Verify: `requirements.txt`

**Interfaces:**
- Consumes: `refresh_chronos_data()`, `publish_forecast()`, `evaluate_chronos()`, and `reconcile_forecasts()`.
- Produces: the four forecast-data commands from the approved spec with exit code `0` on success and `2` on input/data failure. Task 8 adds the two portfolio commands before the final full-suite checkpoint.

- [ ] **Step 1: Write failing CLI parsing and dispatch tests**

Patch the four command functions at their module boundaries and call `perpetual_engine.cli.main()` with each exact argument list. Assert correct `Path` values, exit code `0`, and no calls to existing evaluate/data/backtest handlers. Add one corrupt-config case and assert exit code `2` plus an `Input error` message.

```python
def test_chronos_forecast_dispatch(self):
    with patch("perpetual_engine.chronos.publish_forecast", return_value="a" * 64) as command:
        code = main(["chronos", "forecast", "--config", "config/chronos_v1.json", "--output", "outputs/chronos_v1"])
    self.assertEqual(code, 0)
    command.assert_called_once_with(Path("config/chronos_v1.json"), Path("outputs/chronos_v1"))
```

- [ ] **Step 2: Run CLI tests and verify parser failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosCliTests -v`

Expected: `SystemExit` because `chronos` is not yet a recognized command.

- [ ] **Step 3: Add the four CLI subcommands and lazy dispatch**

Extend `_parser()` with:

```python
chronos = subparsers.add_parser("chronos", help="run Chronos-2 market research")
chronos_commands = chronos.add_subparsers(dest="chronos_command", required=True)
chronos_refresh = chronos_commands.add_parser("refresh")
chronos_refresh.add_argument("--config", type=Path, required=True)
chronos_forecast = chronos_commands.add_parser("forecast")
chronos_forecast.add_argument("--config", type=Path, required=True)
chronos_forecast.add_argument("--output", type=Path, required=True)
chronos_evaluate = chronos_commands.add_parser("evaluate")
chronos_evaluate.add_argument("--config", type=Path, required=True)
chronos_evaluate.add_argument("--output", type=Path, required=True)
chronos_reconcile = chronos_commands.add_parser("reconcile")
chronos_reconcile.add_argument("--config", type=Path, required=True)
chronos_reconcile.add_argument("--forecast-root", type=Path, required=True)
chronos_reconcile.add_argument("--output", type=Path, required=True)
```

Import Chronos modules only inside the matching dispatch branch. Include `forecast_root` in the existing input-error path list. Print only the published ID or output path on success.

- [ ] **Step 4: Run the complete offline unit suite**

Run: `.venv\Scripts\python.exe -m unittest tests.test_chronos -v`

Expected: all Chronos tests pass with the fake predictor and no network access.

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all existing and Chronos tests pass.

- [ ] **Step 5: Verify dependencies and installed-package consistency**

Run: `.venv\Scripts\python.exe -m pip check`

Expected: `No broken requirements found.` Confirm `requirements.txt` still contains `chronos-forecasting==2.3.1` and `transformers>=4.41,<5`; do not add another dependency.

- [ ] **Step 6: Run one cached-model CPU smoke forecast**

Use the frozen model revision with `local_files_only=True`, a deterministic synthetic `(4, 24)` target, five `(24,)` past covariates, and one 12-value future `ECB_DFR`. Call `predict_quantiles(..., prediction_length=12, quantile_levels=[0.1, 0.5, 0.9])` and assert one finite `(4, 12, 3)` output.

Run: `.venv\Scripts\python.exe -c "import numpy as np; from chronos import Chronos2Pipeline; p=Chronos2Pipeline.from_pretrained('amazon/chronos-2',revision='29ec3766d36d6f73f0696f85560a422f50e8498c',device_map='cpu',local_files_only=True); x={'target':np.zeros((4,24),dtype=np.float32),'past_covariates':{k:np.zeros(24,dtype=np.float32) for k in ('ECB_DFR','US_TREASURY_10Y','BRENT_RETURN','BIS_USD_CREDIT_YOY','US_CPI_YOY')},'future_covariates':{'ECB_DFR':np.zeros(12,dtype=np.float32)}}; q,_=p.predict_quantiles([x],prediction_length=12,quantile_levels=[0.1,0.5,0.9]); a=q[0].detach().cpu().numpy(); assert a.shape==(4,12,3) and np.isfinite(a).all(); print(a.shape)"`

Expected: `(4, 12, 3)`.

- [ ] **Step 7: Record the forecast-workflow checkpoint**

If Git is available, run `git add config/chronos_v1.json perpetual_engine/chronos_data.py perpetual_engine/chronos.py perpetual_engine/cli.py tests/test_chronos.py requirements.txt` and `git commit -m "feat: add reproducible Chronos market forecasts"`. If Git remains unavailable, do not initialize it; report the verified files and test results directly.

---

### Task 8: Portfolio P monitoring

**Files:**
- Create: `config/portfolio_p_v1.json`
- Create: `perpetual_engine/portfolio_monitor.py`
- Create: `tests/test_portfolio_monitor.py`
- Modify: `perpetual_engine/cli.py`

**Scope:** Monitor, but do not forecast or trade, `P_US_FACTOR_TREND = 60% SWDA + 15% QDVA + 15% QDVB + 10% DBMG` in EUR with monthly target-weight rebalancing. Bind exact identifiers and listings: `SWDA.MI` / ISIN `IE00B4L5Y983`, `QDVA.DE` / ISIN `IE00BD1F4N50`, `QDVB.DE` / ISIN `IE00BD1F4L37`, and `DBMG.L` / ISIN `LU2951555585`. Do not backfill DBMG with DBMF or an index.

**Interfaces:**
- `load_portfolio_config(path: Path) -> PortfolioConfig`
- `refresh_portfolio_prices(path: Path, *, downloader: Callable | None = None, retrieved_at: datetime | None = None) -> str`
- `portfolio_rows(config: PortfolioConfig, eur_prices: Mapping[str, Mapping[date, float]]) -> tuple[PortfolioRow, ...]`
- `write_portfolio_report(path: Path, output: Path) -> Path`

The config freezes base currency `EUR`, monthly rebalancing, starting value `100`, weights summing exactly to one, source tickers and ISINs, `GBPEUR=X` as EUR-per-GBP FX source, seven-calendar-day month-end staleness, and a data root below the project. `SHORT_LIVE_HISTORY` is true until 36 complete common monthly returns exist. The earlier portfolio `33% SWDA + 27% IWMO + 40% IWQI` remains documented but disabled until the exact `IWQI` listing or ISIN is confirmed.

- [ ] **Step 1: Write one focused failing test**

Use a tiny normalized monthly EUR-price fixture. Assert exact weight validation, first-common-month truncation, weighted monthly return, cumulative value, sample volatility times `sqrt(12)`, maximum drawdown, rolling 12-month correlations, and explicit unavailable 12-month metrics before 12 observations. Monthly return, cumulative value, and drawdown remain available earlier. Include a GBp conversion fixture and reject missing, duplicate, stale, or non-finite prices.

- [ ] **Step 2: Implement the smallest monitor using existing dependencies**

Reuse `yfinance` only in the networked refresh path and the project's existing adjusted-close extraction pattern. Freeze downloaded rows and hashes before calculation. For each completed month use the last adjusted close no more than seven calendar days before month end. Convert `DBMG.L / 100 * GBPEUR=X`, where FX is the last quote on or before DBMG's price date and is defined as EUR per GBP. The offline report reads only the frozen price/FX artifact. Use ordinary weighted monthly returns; do not introduce an optimizer, covariance shrinkage, or synthetic portfolio quantiles.

- [ ] **Step 3: Add portfolio refresh/report CLI dispatch**

Add `chronos portfolio-refresh --config ...` as a networked command and `chronos portfolio-report --config ... --output ...` as an offline command.

- `portfolio_monthly.csv`: `month,SWDA,QDVA,QDVB,DBMG,portfolio_return,cumulative_value,drawdown,trailing_volatility_12m`;
- `portfolio_metrics.csv`: `first_month,last_month,count,cumulative_return,annualized_volatility,max_drawdown`;
- `portfolio_correlations.csv`: `month,pair,count,rolling_correlation_12m` for all six unordered pairs `SWDA-QDVA`, `SWDA-QDVB`, `SWDA-DBMG`, `QDVA-QDVB`, `QDVA-DBMG`, and `QDVB-DBMG`;
- `manifest.json`: config/input/output hashes, exact identifiers, weights, FX rule, first common month, and `SHORT_LIVE_HISTORY`.

- [ ] **Step 4: Verify**

Run the focused portfolio test, all Chronos tests, the full suite, and `pip check`. A live refresh failure must leave the previous frozen input usable.

- [ ] **Step 5: Record the final checkpoint**

Do not initialize Git. Report the final file list and exact verification results.
