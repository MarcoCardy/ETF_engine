# Task 1 report: Configuration and target-data contract

## Implementation summary

- Added the frozen `config/chronos_v1.json` contract with the specified Chronos model, target, covariate, evaluation, and bootstrap values.
- Added `perpetual_engine.chronos_data` with frozen `SourceSpec`, `ChronosConfig`, and `MonthlyTable` dataclasses.
- `load_chronos_config()` validates the frozen v1 model/prediction/quantile/evaluation contract, preserves configured source order, requires each v1 source exactly once, permits only registered parser names, computes a canonical JSON SHA-256, and rejects configured paths escaping the project root.
- `load_target_table()` validates the four-sleeve manifest schema and target hash, requires the exact leading target header order, validates finite returns and unique contiguous month ends, and returns an immutable `4 x N` NumPy array.
- Added real-file fixtures and regression coverage for valid data, manifest hash mismatch, duplicate/missing/non-month-end months, non-finite values, and reordered target columns.

## RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v
```

Output: 4 errors, each `ModuleNotFoundError: No module named 'perpetual_engine.chronos_data'`.

Why it failed: the requested loader module had not yet been created. This was the expected missing-feature failure.

## GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v
```

Output:

```text
Ran 4 tests in 0.280s

OK
```

Additional production-artifact check loaded `config/chronos_v1.json` and its manifest-validated target table successfully: `(4, 240) 2006-06-30 2026-05-31`.

## Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -q
```

Result: exit code 0 (30.8 seconds). The suite emitted its existing expected `Input error` diagnostics from fail-closed CLI tests; no failures occurred.

## Files changed

- `config/chronos_v1.json`
- `perpetual_engine/chronos_data.py`
- `tests/test_chronos.py`

## Self-review

- Confirmed all configured filesystem paths resolve below the configuration-derived project root.
- Confirmed source order is retained from configuration and target order is locked to the required four sleeves.
- Confirmed manifest hashing uses `sha256_file()`, configuration hashing uses `canonical_json()`, and returned target arrays are read-only.
- Confirmed actual four-sleeve output validates under the new contract.

## Concerns

None. The project is not a Git repository; commits: none (no repository).

## Fix round 1/5

### Implementation summary

- Locked each required v1 source ID to its exact parser and role.
- Restricted `known_future` to `ECB_DFR`; all other configured sources, including future extensions, must be `past_only`.
- Expanded the frozen-contract test to assert every `ChronosConfig` field, all source tuples, and the complete fixture month tuple.
- Added invalid parser/role mapping coverage for all required past-only sources and an additional registered-parser source.

### RED

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v
```

Output: 5 focused tests ran; the new mapping test failed five subcases with `AssertionError: ValueError not raised` for a swapped ECB parser, three required sources changed to `known_future`, and an additional `known_future` source.

Why it failed: the previous validation accepted any registered parser for a required source and did not constrain the `known_future` role.

### GREEN

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_chronos.ChronosConfigTests -v
```

Output:

```text
Ran 5 tests in 0.332s

OK
```

### Full suite

Command:

```powershell
.venv\Scripts\python.exe -m unittest discover -q
```

Result: exit code 0 (31.0 seconds). Existing fail-closed CLI tests emitted their expected `Input error` diagnostics; no failures occurred.

### Files changed

- `perpetual_engine/chronos_data.py`
- `tests/test_chronos.py`
- `.superpowers/sdd/2026-09-03-chronos-2-market-forecast-plan/task-1-report.md`

### Self-review

- Required source IDs now reject parser or role substitutions before a configuration can be used.
- `known_future` is impossible for every source except the fixed ECB source, closing the cited leakage path.
- The tests derive all expected frozen values independently and assert the entire three-month fixture chronology.

### Concerns

None. Git remains absent; commits: none (no repository).
