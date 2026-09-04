# Task 2 Brief — Point-in-time row and frozen manifest contracts

Source plan: `docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md`  
Binding spec: `docs/superpowers/specs/2026-08-21-perpetual-engine-historical-backtest-design.md`, especially §§5, 9, 10, 18–20.

## Files

- Create: `perpetual_engine/point_in_time.py`
- Create: `perpetual_engine/data_sources.py`
- Create: `tests/test_point_in_time.py`
- Create: `tests/test_data_sources.py`
- Modify: `perpetual_engine/io.py` only to reuse deterministic JSON/hash helpers where appropriate.

## Required interfaces

```python
@dataclass(frozen=True)
class ObservationRow:
    series_id: str
    observation_date: date
    period_end: date
    available_at: datetime
    value: Decimal
    unit: str
    source_url: str
    retrieved_at: datetime
    source_hash: str
    vintage_status: str = "CURRENT_VINTAGE_RESEARCH"
    quality_flags: tuple[str, ...] = ()

@dataclass(frozen=True)
class SourceArtifact:
    source_url: str
    retrieved_at: datetime
    source_hash: str
    local_path: Path
    byte_count: int
    parser_version: str

def asof_select(rows: Iterable[ObservationRow], decision_at: datetime) -> ObservationRow | None: ...
def validate_rows(rows: Iterable[ObservationRow]) -> tuple[ObservationRow, ...]: ...
def freeze_bytes(content: bytes, source_url: str, retrieved_at: datetime, raw_dir: Path, parser_version: str) -> SourceArtifact: ...
def load_observation_csv(path: Path) -> tuple[ObservationRow, ...]: ...
def write_observation_csv(rows: Iterable[ObservationRow], path: Path) -> None: ...
```

## Required behavior

- All datetimes are timezone-aware and normalized to UTC.
- `asof_select` chooses the eligible row with maximum `(available_at, observation_date, retrieved_at, source_hash)` and only when `available_at <= decision_at`.
- Joining or selecting by observation date alone is forbidden.
- Validate finite Decimal values, non-empty IDs/units/provenance, 64-character lowercase hex hashes, `period_end >= observation_date`, `retrieved_at >= available_at`, and unique `(series_id, observation_date, source_hash)`.
- `freeze_bytes` hashes content with SHA-256, writes under `<raw_dir>/<sha256>/<original_filename>`, and writes a sorted-key compact `manifest.json` in the same hash directory. Existing identical content is reused byte-for-byte. Changed content creates a new hash directory and never overwrites an earlier vintage.
- `load_observation_csv` and writer use exact stable columns matching the dataclass; decimals and timestamps are strings; quality flags are a sorted `|`-joined string.
- No network call belongs in `point_in_time.py`. `data_sources.py` may define the future network boundary but this task only implements freezing, not provider downloads/parsers.
- No database, cache service, provider interface hierarchy, or dependency is added.

## TDD sequence and required sentinels

1. Write tests first and run focused RED.
2. Implement rows/as-of/validation and run GREEN.
3. Write freezing/manifest tests first and run RED.
4. Implement freeze/CSV functions and run GREEN.
5. Run the complete suite once.

```python
def test_asof_never_selects_future_release(self):
    row = observation(available_at="2008-02-01T23:59:59+00:00")
    self.assertIsNone(asof_select((row,), utc("2008-02-01T12:00:00+00:00")))
    self.assertEqual(asof_select((row,), utc("2008-03-01T00:00:00+00:00")), row)

def test_changed_bytes_create_new_vintage(self):
    first = freeze_bytes(b"one", URL, NOW, raw_dir, "1")
    second = freeze_bytes(b"two", URL, NOW, raw_dir, "1")
    self.assertNotEqual(first.source_hash, second.source_hash)
    self.assertTrue(first.local_path.exists())
    self.assertTrue(second.local_path.exists())

def test_csv_round_trip_is_byte_stable(self):
    write_observation_csv(rows, first_path)
    write_observation_csv(load_observation_csv(first_path), second_path)
    self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
```

Run focused: `.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v`  
Run full: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

## Constraints

- Existing gross-fiscal names from Task 1 are the only valid names.
- Use `Decimal`; never convert observation values through float.
- Use `apply_patch` for edits.
- Do not initialize Git or dispatch subagents.
