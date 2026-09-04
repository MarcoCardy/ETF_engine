# Task 2 report — point-in-time rows and frozen manifests

## Result

Implemented the generic, offline point-in-time row contract, deterministic observation CSV I/O, and content-addressed frozen raw artifacts. No provider parser, download, provider hierarchy, database, cache, or new dependency was added.

## Files changed

- Created `perpetual_engine/point_in_time.py`
- Created `perpetual_engine/data_sources.py`
- Created `tests/test_point_in_time.py`
- Created `tests/test_data_sources.py`
- Modified `perpetual_engine/io.py` to add the reused compact, sorted-key `canonical_json()` helper; `normalized_json()` continues to preserve its existing behavior.

## TDD evidence

### Row/as-of RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time -v
```

Output:

```text
test_point_in_time (unittest.loader._FailedTest.test_point_in_time) ... ERROR

======================================================================
ERROR: test_point_in_time (unittest.loader._FailedTest.test_point_in_time)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_point_in_time
Traceback (most recent call last):
  File "C:\\Users\\marco\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\Lib\\unittest\\loader.py", line 137, in loadTestsFromName
    module = __import__(module_name)
             ^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\\Users\\marco\\OneDrive\\Desktop\\ETF_infazione\\tests\\test_point_in_time.py", line 5, in <module>
    from perpetual_engine.point_in_time import ObservationRow, asof_select, validate_rows
ModuleNotFoundError: No module named 'perpetual_engine.point_in_time'

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (errors=1)
```

### Row/as-of GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time -v
```

Output:

```text
test_asof_never_selects_future_release (tests.test_point_in_time.PointInTimeTests.test_asof_never_selects_future_release) ... ok
test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers (tests.test_point_in_time.PointInTimeTests.test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers) ... ok
test_validation_normalizes_timestamps_to_utc (tests.test_point_in_time.PointInTimeTests.test_validation_normalizes_timestamps_to_utc) ... ok
test_validation_rejects_duplicate_series_observation_and_vintage (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_duplicate_series_observation_and_vintage) ... ok
test_validation_rejects_invalid_rows (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_invalid_rows) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.002s

OK
```

### Freeze/manifest and CSV RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
test_point_in_time (unittest.loader._FailedTest.test_point_in_time) ... ERROR
test_data_sources (unittest.loader._FailedTest.test_data_sources) ... ERROR

======================================================================
ERROR: test_point_in_time (unittest.loader._FailedTest.test_point_in_time)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_point_in_time
Traceback (most recent call last):
  File "C:\\Users\\marco\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\Lib\\unittest\\loader.py", line 137, in loadTestsFromName
    module = __import__(module_name)
             ^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\\Users\\marco\\OneDrive\\Desktop\\ETF_infazione\\tests\\test_point_in_time.py", line 5, in <module>
    from perpetual_engine.point_in_time import (
ImportError: cannot import name 'load_observation_csv' from 'perpetual_engine.point_in_time' (C:\\Users\\marco\\OneDrive\\Desktop\\ETF_infazione\\perpetual_engine\\point_in_time.py)

======================================================================
ERROR: test_data_sources (unittest.loader._FailedTest.test_data_sources)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_data_sources
Traceback (most recent call last):
  File "C:\\Users\\marco\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\Lib\\unittest\\loader.py", line 137, in loadTestsFromName
    module = __import__(module_name)
             ^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\\Users\\marco\\OneDrive\\Desktop\\ETF_infazione\\tests\\test_data_sources.py", line 8, in <module>
    from perpetual_engine.data_sources import freeze_bytes
ModuleNotFoundError: No module named 'perpetual_engine.data_sources'

----------------------------------------------------------------------
Ran 2 tests in 0.001s

FAILED (errors=2)
```

### Freeze/manifest and CSV GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
test_asof_never_selects_future_release (tests.test_point_in_time.PointInTimeTests.test_asof_never_selects_future_release) ... ok
test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers (tests.test_point_in_time.PointInTimeTests.test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers) ... ok
test_csv_output_is_stable_when_rows_arrive_in_a_different_order (tests.test_point_in_time.PointInTimeTests.test_csv_output_is_stable_when_rows_arrive_in_a_different_order) ... ok
test_csv_round_trip_is_byte_stable (tests.test_point_in_time.PointInTimeTests.test_csv_round_trip_is_byte_stable) ... ok
test_validation_normalizes_timestamps_to_utc (tests.test_point_in_time.PointInTimeTests.test_validation_normalizes_timestamps_to_utc) ... ok
test_validation_rejects_duplicate_series_observation_and_vintage (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_duplicate_series_observation_and_vintage) ... ok
test_validation_rejects_invalid_rows (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_invalid_rows) ... ok
test_changed_bytes_create_new_vintage (tests.test_data_sources.DataSourceTests.test_changed_bytes_create_new_vintage) ... ok
test_freeze_writes_content_and_deterministic_manifest (tests.test_data_sources.DataSourceTests.test_freeze_writes_content_and_deterministic_manifest) ... ok
test_identical_bytes_reuse_existing_vintage_without_rewrite (tests.test_data_sources.DataSourceTests.test_identical_bytes_reuse_existing_vintage_without_rewrite) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.106s

OK
```

### Deterministic-row-order RED and GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time -v
```

RED output:

```text
test_asof_never_selects_future_release (tests.test_point_in_time.PointInTimeTests.test_asof_never_selects_future_release) ... ok
test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers (tests.test_point_in_time.PointInTimeTests.test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers) ... ok
test_csv_output_is_stable_when_rows_arrive_in_a_different_order (tests.test_point_in_time.PointInTimeTests.test_csv_output_is_stable_when_rows_arrive_in_a_different_order) ... FAIL
test_csv_round_trip_is_byte_stable (tests.test_point_in_time.PointInTimeTests.test_csv_round_trip_is_byte_stable) ... ok
test_validation_normalizes_timestamps_to_utc (tests.test_point_in_time.PointInTimeTests.test_validation_normalizes_timestamps_to_utc) ... ok
test_validation_rejects_duplicate_series_observation_and_vintage (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_duplicate_series_observation_and_vintage) ... ok
test_validation_rejects_invalid_rows (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_invalid_rows) ... ok

======================================================================
FAIL: test_csv_output_is_stable_when_rows_arrive_in_a_different_order (tests.test_point_in_time.PointInTimeTests.test_csv_output_is_stable_when_rows_arrive_in_a_different_order)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\\Users\\marco\\OneDrive\\Desktop\\ETF_infazione\\tests\\test_point_in_time.py", line 127, in test_csv_output_is_stable_when_rows_arrive_in_a_different_order
    self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
AssertionError: b'ser[122 chars]s\\r\\nCPI,2007-12-31,2007-12-31,2008-02-01T23:5[399 chars]\\r\\n' != b'ser[122 chars]s\\r\\nERP,2007-12-31,2007-12-31,2008-02-01T23:5[399 chars]\\r\\n'

----------------------------------------------------------------------
Ran 7 tests in 0.117s
FAILED (failures=1)
```

After sorting validated rows by their complete stable contract key, the focused command above was re-run as part of the 10-test GREEN checkpoint and passed.

## Full-suite checkpoint

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 42 tests in 1.285s

OK
```

## Self-review

- `asof_select()` normalizes its decision timestamp and only chooses rows where `available_at <= decision_at`; its key is exactly `(available_at, observation_date, retrieved_at, source_hash)`.
- `validate_rows()` rejects non-finite `Decimal` values, missing series/unit/source URL, invalid hashes, bad temporal ordering, naïve timestamps, and duplicate `(series_id, observation_date, source_hash)` identities; it returns UTC-normalized immutable rows.
- CSV output has dataclass-field column order, text decimals/timestamps, sorted pipe-joined flags, and deterministic record order. Load validates the same contract and round-trips byte-for-byte.
- `freeze_bytes()` SHA-256-addresses immutable raw content, derives a safe URL filename, does not overwrite existing raw files/manifests, and writes compact sorted-key manifests.
- The new modules make no network calls. `data_sources.py` is solely the future boundary for raw artifact freezing.

## Concerns

None for this task's specified scope. The manifest is intentionally retained unchanged when the same content hash is frozen again, preserving the original frozen vintage byte-for-byte.

## Fix round 1 — frozen-vintage integrity and immutable quality flags

### Findings addressed

1. Re-freezing now hashes an existing raw file with `sha256_file()` before reuse and raises `ValueError("corrupt frozen artifact: ...")` if it no longer matches the content-address directory.
2. A source URL whose filename is `manifest.json` writes raw content as the deterministic reserved-name escape `raw-manifest.json`, leaving the required root `manifest.json` intact.
3. Every freeze persists its exact provenance record in `<hash>/provenance/<sha256(canonical-record)>.json`. The root manifest remains the first immutable record; later sources with identical bytes never overwrite it, and each `SourceArtifact` fields exactly match its dedicated persisted record.
4. `ObservationRow.__post_init__()` snapshots mutable flag iterables into a real tuple and rejects strings, non-iterables, and empty/non-string flags. The sorted CSV representation remains byte deterministic.
5. Added isolated tests for each secondary as-of tie-breaker: observation date, retrieval time, and source hash. They passed immediately because the existing key was already exact; no implementation change was required for those three checks.

### Files changed in this round

- Modified `perpetual_engine/data_sources.py`
- Modified `perpetual_engine/point_in_time.py`
- Modified `tests/test_data_sources.py`
- Modified `tests/test_point_in_time.py`

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
test_asof_never_selects_future_release (tests.test_point_in_time.PointInTimeTests.test_asof_never_selects_future_release) ... ok
test_asof_tiebreaks_on_observation_date (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_observation_date) ... ok
test_asof_tiebreaks_on_retrieval_time (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_retrieval_time) ... ok
test_asof_tiebreaks_on_source_hash (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_source_hash) ... ok
test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers (tests.test_point_in_time.PointInTimeTests.test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers) ... ok
test_csv_output_is_stable_when_rows_arrive_in_a_different_order (tests.test_point_in_time.PointInTimeTests.test_csv_output_is_stable_when_rows_arrive_in_a_different_order) ... ok
test_csv_round_trip_is_byte_stable (tests.test_point_in_time.PointInTimeTests.test_csv_round_trip_is_byte_stable) ... ok
test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic (tests.test_point_in_time.PointInTimeTests.test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic) ... FAIL
test_validation_normalizes_timestamps_to_utc (tests.test_point_in_time.PointInTimeTests.test_validation_normalizes_timestamps_to_utc) ... ok
test_validation_rejects_duplicate_series_observation_and_vintage (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_duplicate_series_observation_and_vintage) ... ok
test_validation_rejects_invalid_rows (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_invalid_rows) ... ok
test_changed_bytes_create_new_vintage (tests.test_data_sources.DataSourceTests.test_changed_bytes_create_new_vintage) ... ok
test_freeze_writes_content_and_deterministic_manifest (tests.test_data_sources.DataSourceTests.test_freeze_writes_content_and_deterministic_manifest) ... ok
test_identical_bytes_reuse_existing_vintage_without_rewrite (tests.test_data_sources.DataSourceTests.test_identical_bytes_reuse_existing_vintage_without_rewrite) ... ok
test_refreezing_rejects_a_corrupted_existing_raw_file (tests.test_data_sources.DataSourceTests.test_refreezing_rejects_a_corrupted_existing_raw_file) ... FAIL
test_same_bytes_from_different_urls_keep_each_artifact_provenance (tests.test_data_sources.DataSourceTests.test_same_bytes_from_different_urls_keep_each_artifact_provenance) ... FAIL
test_source_named_manifest_json_does_not_replace_the_manifest (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_does_not_replace_the_manifest) ... FAIL

======================================================================
FAIL: test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic (tests.test_point_in_time.PointInTimeTests.test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic)
----------------------------------------------------------------------
AssertionError: ['late', 'checked', 'mutated'] != ('late', 'checked')

======================================================================
FAIL: test_refreezing_rejects_a_corrupted_existing_raw_file (tests.test_data_sources.DataSourceTests.test_refreezing_rejects_a_corrupted_existing_raw_file)
----------------------------------------------------------------------
AssertionError: ValueError not raised

======================================================================
FAIL: test_same_bytes_from_different_urls_keep_each_artifact_provenance (tests.test_data_sources.DataSourceTests.test_same_bytes_from_different_urls_keep_each_artifact_provenance)
----------------------------------------------------------------------
AssertionError: Element counts were not equal:
First has 0, Second has 1:  {'byte_count': 3, 'parser_version': '1', 'retrieved_at': '2026-08-22T10:30:00+00:00', 'source_hash': '7692c3ad3540bb803c020b3aee66cd8887123234ea0c6e7143c0add73ff431ed', 'source_url': 'https://example.test/one.csv'}
First has 0, Second has 1:  {'byte_count': 3, 'parser_version': '2', 'retrieved_at': '2026-08-23T10:30:00+00:00', 'source_hash': '7692c3ad3540bb803c020b3aee66cd8887123234ea0c6e7143c0add73ff431ed', 'source_url': 'https://example.test/two.csv'}

======================================================================
FAIL: test_source_named_manifest_json_does_not_replace_the_manifest (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_does_not_replace_the_manifest)
----------------------------------------------------------------------
AssertionError: 'manifest.json' != 'raw-manifest.json'
- manifest.json
+ raw-manifest.json
? ++++

----------------------------------------------------------------------
Ran 17 tests in 0.082s

FAILED (failures=4)
```

### GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
test_asof_never_selects_future_release (tests.test_point_in_time.PointInTimeTests.test_asof_never_selects_future_release) ... ok
test_asof_tiebreaks_on_observation_date (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_observation_date) ... ok
test_asof_tiebreaks_on_retrieval_time (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_retrieval_time) ... ok
test_asof_tiebreaks_on_source_hash (tests.test_point_in_time.PointInTimeTests.test_asof_tiebreaks_on_source_hash) ... ok
test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers (tests.test_point_in_time.PointInTimeTests.test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers) ... ok
test_csv_output_is_stable_when_rows_arrive_in_a_different_order (tests.test_point_in_time.PointInTimeTests.test_csv_output_is_stable_when_rows_arrive_in_a_different_order) ... ok
test_csv_round_trip_is_byte_stable (tests.test_point_in_time.PointInTimeTests.test_csv_round_trip_is_byte_stable) ... ok
test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic (tests.test_point_in_time.PointInTimeTests.test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic) ... ok
test_validation_normalizes_timestamps_to_utc (tests.test_point_in_time.PointInTimeTests.test_validation_normalizes_timestamps_to_utc) ... ok
test_validation_rejects_duplicate_series_observation_and_vintage (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_duplicate_series_observation_and_vintage) ... ok
test_validation_rejects_invalid_rows (tests.test_point_in_time.PointInTimeTests.test_validation_rejects_invalid_rows) ... ok
test_changed_bytes_create_new_vintage (tests.test_data_sources.DataSourceTests.test_changed_bytes_create_new_vintage) ... ok
test_freeze_writes_content_and_deterministic_manifest (tests.test_data_sources.DataSourceTests.test_freeze_writes_content_and_deterministic_manifest) ... ok
test_identical_bytes_reuse_existing_vintage_without_rewrite (tests.test_data_sources.DataSourceTests.test_identical_bytes_reuse_existing_vintage_without_rewrite) ... ok
test_refreezing_rejects_a_corrupted_existing_raw_file (tests.test_data_sources.DataSourceTests.test_refreezing_rejects_a_corrupted_existing_raw_file) ... ok
test_same_bytes_from_different_urls_keep_each_artifact_provenance (tests.test_data_sources.DataSourceTests.test_same_bytes_from_different_urls_keep_each_artifact_provenance) ... ok
test_source_named_manifest_json_does_not_replace_the_manifest (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_does_not_replace_the_manifest) ... ok

----------------------------------------------------------------------
Ran 17 tests in 0.219s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 49 tests in 0.657s

OK
```

### Fix-round self-review and concerns

- The reuse integrity check happens before any manifest/provenance write, so corruption fails closed without changing frozen metadata.
- Reserved-name escaping is intentionally limited to the single path reserved by this contract; all other URL basenames remain unchanged.
- A provenance record’s filename is the SHA-256 of its canonical JSON bytes, so the path is deterministic and duplicate freezes are reused without a rewrite.
- No network behavior or provider parser was added.

Concerns: none in the requested scope.

## Fix round 5 — complete preventive Windows basename validation

### Finding addressed

- The decoded basename is validated before source hashing and any filesystem creation. It rejects every Windows-reserved character (`< > : " / \\ | ? *`), all control characters U+0000–U+001F, and empty/dot-only forms already normalized by the collision key.
- Device names are rejected case-insensitively, including extensions and trailing spaces/dots: `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, and `LPT1`–`LPT9`.
- Reserved internal-name routing remains after this validation and continues to use the deterministic `raw/` namespace.

### Files changed in this round

- Modified `perpetual_engine/data_sources.py`
- Modified `tests/test_data_sources.py`

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
```

Output:

```text
test_windows_device_names_are_rejected_with_extensions_before_writing ... FAIL
test_windows_reserved_characters_and_controls_are_rejected_before_writing ... FAIL

ERROR: test_windows_reserved_characters_and_controls_are_rejected_before_writing (... source_url='https://example.test/artifacts/%3Csource%3E.csv')
OSError: [Errno 22] Invalid argument: '...\\<source>.csv'

ERROR: test_windows_reserved_characters_and_controls_are_rejected_before_writing (... source_url='https://example.test/artifacts/source%1F.csv')
OSError: [Errno 22] Invalid argument: '...\\source\\x1f.csv'

FAIL: test_windows_reserved_characters_and_controls_are_rejected_before_writing (... source_url='https://example.test/artifacts/source%3A.csv')
AssertionError: ValueError not raised

FAIL: test_windows_device_names_are_rejected_with_extensions_before_writing
AssertionError: ValueError not raised

----------------------------------------------------------------------
Ran 14 tests in 0.449s

FAILED (failures=10, errors=2)
```

### GREEN

Focused commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
Ran 14 tests in 0.188s

OK

Ran 25 tests in 0.262s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 57 tests in 0.746s

OK
```

### Fix-round self-review and concerns

- `_validated_windows_filename_key()` is a deterministic standard-library-only gate and runs before the SHA-256 directory is constructed.
- Tests cover `%3A`, `%3C...%3E`, `%1F`, `CON`, `CON.txt`, every required device-name family, and assert that the supplied raw root remains empty on each rejection.
- No provider/network behavior, dependency, or wider path abstraction was added.

Concerns: none in the requested scope.

## Fix round 4 — safe final URL-segment extraction

### Finding addressed

- `freeze_bytes()` no longer calls `Path(urlparse(url).path).name` for the source name. It first splits the URL path on `/`, decodes only the final segment with `unquote()`, then validates it before deriving the artifact hash directory.
- Final segments `/.`, `/%2E`, `/%2E%2E`, and `/%20` all normalize to an unusable Windows name and fail before any output is created.
- A decoded `/`, `\\`, or NUL in the final segment fails before writes, preventing encoded separator/traversal-style paths from becoming filesystem paths.

### Files changed in this round

- Modified `perpetual_engine/data_sources.py`
- Modified `tests/test_data_sources.py`

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
```

Output:

```text
test_decoded_path_separator_in_final_url_segment_is_rejected_before_writing ... FAIL
test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing ... FAIL

FAIL: test_decoded_path_separator_in_final_url_segment_is_rejected_before_writing
AssertionError: ValueError not raised

FAIL: test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing (... source_url='https://example.test/artifacts/.')
AssertionError: ValueError not raised

FAIL: test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing (... source_url='https://example.test/artifacts/%2E')
AssertionError: ValueError not raised

FAIL: test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing (... source_url='https://example.test/artifacts/%2E%2E')
AssertionError: ValueError not raised

FAIL: test_dot_only_and_decoded_empty_url_segments_are_rejected_before_writing (... source_url='https://example.test/artifacts/%20')
AssertionError: ValueError not raised

----------------------------------------------------------------------
Ran 12 tests in 0.409s

FAILED (failures=5)
```

### GREEN

Focused commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
Ran 12 tests in 0.252s

OK

Ran 23 tests in 0.305s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 55 tests in 0.777s

OK
```

### Fix-round self-review and concerns

- URL extraction and validation now precede source hashing and `artifact_dir.mkdir()`, so the rejected inputs leave the supplied raw root empty.
- The raw path remains based only on a validated decoded filename; `Path` is used afterwards only to combine known-safe filesystem components.
- No provider/network behavior or dependency was added.

Concerns: none in the requested scope.

## Fix round 3 — Windows trailing-dot and trailing-space aliases

### Finding addressed

- Internal-name matching now uses `filename.rstrip(" .").casefold()`. Therefore `manifest.json.`, `manifest.json `, `MANIFEST.JSON.`, `provenance.`, and `raw.` all enter the reserved raw namespace rather than colliding with internal paths on Windows.
- An empty key after Windows normalization (for example a source basename consisting only of spaces) raises `ValueError` before `artifact_dir.mkdir()`, so no partial artifact is created.
- The raw-path decision remains entirely before the first write; ordinary basenames remain stable.

### Files changed in this round

- Modified `perpetual_engine/data_sources.py`
- Modified `tests/test_data_sources.py`

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
```

Output:

```text
test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts (tests.test_data_sources.DataSourceTests.test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts) ... ok
test_changed_bytes_create_new_vintage (tests.test_data_sources.DataSourceTests.test_changed_bytes_create_new_vintage) ... ok
test_empty_windows_collision_name_is_rejected_before_creating_artifacts (tests.test_data_sources.DataSourceTests.test_empty_windows_collision_name_is_rejected_before_creating_artifacts) ... ERROR
test_freeze_writes_content_and_deterministic_manifest (tests.test_data_sources.DataSourceTests.test_freeze_writes_content_and_deterministic_manifest) ... ok
test_identical_bytes_reuse_existing_vintage_without_rewrite (tests.test_data_sources.DataSourceTests.test_identical_bytes_reuse_existing_vintage_without_rewrite) ... ok
test_non_reserved_filename_keeps_its_stable_original_path (tests.test_data_sources.DataSourceTests.test_non_reserved_filename_keeps_its_stable_original_path) ... ok
test_refreezing_rejects_a_corrupted_existing_raw_file (tests.test_data_sources.DataSourceTests.test_refreezing_rejects_a_corrupted_existing_raw_file) ... ok
test_same_bytes_from_different_urls_keep_each_artifact_provenance (tests.test_data_sources.DataSourceTests.test_same_bytes_from_different_urls_keep_each_artifact_provenance) ... ok
test_source_named_manifest_json_uses_reserved_raw_namespace (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_uses_reserved_raw_namespace) ... ok
test_windows_trailing_dot_and_space_variants_use_reserved_raw_namespace (tests.test_data_sources.DataSourceTests.test_windows_trailing_dot_and_space_variants_use_reserved_raw_namespace) ... FAIL

======================================================================
ERROR: test_empty_windows_collision_name_is_rejected_before_creating_artifacts
----------------------------------------------------------------------
PermissionError: [Errno 13] Permission denied: '...\\239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5\\ '

======================================================================
FAIL: test_windows_trailing_dot_and_space_variants_use_reserved_raw_namespace
----------------------------------------------------------------------
AssertionError: '5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9' != 'raw'
- 5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9
+ raw

----------------------------------------------------------------------
Ran 10 tests in 0.197s

FAILED (failures=1, errors=1)
```

### GREEN

Focused commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
Ran 10 tests in 0.224s

OK

Ran 21 tests in 0.247s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 53 tests in 0.770s

OK
```

### Fix-round self-review and concerns

- The canonical collision key only changes the internal-reserved comparison; it does not rename normal raw paths.
- The malformed-name test confirms no hash directory exists after rejection.
- No provider/network behavior or external dependency was added.

Concerns: none in the requested scope.

## Fix round 2 — case-insensitive reserved artifact names

### Findings addressed

- Raw URLs named `manifest.json` in any casing now use the internal `raw/` namespace rather than colliding with the root manifest.
- The root `provenance/` directory and the internal `raw/` namespace are also reserved case-insensitively. Sources named `provenance`, `raw`, or any case variant use the same raw namespace.
- A reserved raw filename is deterministic: `<hash>/raw/<sha256(source_url UTF-8)>.bin`. All non-reserved names retain their exact original basename and root location.
- The collision tests assert each hash directory contains only the distinct root entries `manifest.json`, `provenance`, and `raw`; this rules out partial colliding raw files.

### Files changed in this round

- Modified `perpetual_engine/data_sources.py`
- Modified `tests/test_data_sources.py`

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
```

Output:

```text
test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts (tests.test_data_sources.DataSourceTests.test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts) ... FAIL
test_changed_bytes_create_new_vintage (tests.test_data_sources.DataSourceTests.test_changed_bytes_create_new_vintage) ... ok
test_freeze_writes_content_and_deterministic_manifest (tests.test_data_sources.DataSourceTests.test_freeze_writes_content_and_deterministic_manifest) ... ok
test_identical_bytes_reuse_existing_vintage_without_rewrite (tests.test_data_sources.DataSourceTests.test_identical_bytes_reuse_existing_vintage_without_rewrite) ... ok
test_non_reserved_filename_keeps_its_stable_original_path (tests.test_data_sources.DataSourceTests.test_non_reserved_filename_keeps_its_stable_original_path) ... ok
test_refreezing_rejects_a_corrupted_existing_raw_file (tests.test_data_sources.DataSourceTests.test_refreezing_rejects_a_corrupted_existing_raw_file) ... ok
test_same_bytes_from_different_urls_keep_each_artifact_provenance (tests.test_data_sources.DataSourceTests.test_same_bytes_from_different_urls_keep_each_artifact_provenance) ... ok
test_source_named_manifest_json_uses_reserved_raw_namespace (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_uses_reserved_raw_namespace) ... FAIL

======================================================================
FAIL: test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts (tests.test_data_sources.DataSourceTests.test_case_insensitive_reserved_names_never_collide_or_leave_partial_artifacts)
----------------------------------------------------------------------
AssertionError: '5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9' != 'raw'
- 5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9
+ raw

======================================================================
FAIL: test_source_named_manifest_json_uses_reserved_raw_namespace (tests.test_data_sources.DataSourceTests.test_source_named_manifest_json_uses_reserved_raw_namespace)
----------------------------------------------------------------------
AssertionError: '239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5' != 'raw'
- 239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5
+ raw

----------------------------------------------------------------------
Ran 8 tests in 0.112s

FAILED (failures=2)
```

### GREEN

Focused commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_data_sources -v
.venv\Scripts\python.exe -m unittest tests.test_point_in_time tests.test_data_sources -v
```

Output:

```text
Ran 8 tests in 0.188s

OK

Ran 19 tests in 0.241s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
----------------------------------------------------------------------
Ran 51 tests in 0.706s

OK
```

### Fix-round self-review and concerns

- `casefold()` is used only for the fixed internal-name set: `manifest.json`, `provenance`, and `raw`.
- The URL hash prevents collisions among case variants or among reserved source URLs in the internal raw directory.
- Normal raw names remain unchanged, preserving the Task 2 path contract where no reserved collision exists.

Concerns: none in the requested scope.
