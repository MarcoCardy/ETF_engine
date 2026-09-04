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
    {"id": "BIS_USD_CREDIT_YOY", "url": "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip", "parser": "bis_gli", "role": "past_only"}
  ],
  "evaluation": {"origins": 36, "reported_horizons": [1, 3, 6, 12]},
  "bootstrap": {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95}
}
```

- [ ] **Step 4: Implement strict configuration and target loading**

Use frozen dataclasses and NumPy arrays. Resolve every configured path relative to the project root (`config_path.resolve().parent.parent`), require it to remain below that root with `Path.is_relative_to`, accept only the exact model/device/prediction/quantile values above, and require the four v1 source IDs once each. Permit additional unique source IDs only when their parser name is registered explicitly; derive forecasting and ablation order from `config.sources` rather than a hard-coded column list.

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

