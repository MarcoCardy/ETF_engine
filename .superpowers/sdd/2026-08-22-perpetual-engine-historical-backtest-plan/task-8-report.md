# Task 8 implementation report — refresh, offline CLI, and deterministic outputs

## Final gate fix — signal coverage and annual handoff

The two remaining Important findings were reproduced with failing tests before the minimum production changes:

- DGS10 now contributes a deterministic signal-coverage end to `expected_through`; DFII10 contributes from the first February 2003 allocation, whose decision follows January 2003. A month counts only when at least one observation from that month is available by that month-end decision under the existing PIT timestamp rule.
- DGS10 coverage must be contiguous from December 1978. DFII10 coverage must be contiguous from January 2003. Internal monthly gaps fail refresh instead of silently returning `None` and disabling the Treasury/TIPS rules.
- The bundle loop independently fails closed if Treasury is absent in any allocation month, or if TIPS is absent from February 2003 onward.
- A short signal tail reduces the common bundle end; the existing explicit 45-day staleness check then rejects a stale shortened bundle. Manifest and bundle end fields remain identical.
- The Damodaran annual parser now requires its continuous parsed history to end exactly in 2007. Histories ending in 1978 or 2006 are rejected.
- The official-shaped annual fixture is a continuous legacy `.xls` from 1977 through 2007; no runtime or test dependency was added.

### Final gate RED evidence

```text
.venv\Scripts\python.exe -m unittest \
  tests.test_damodaran.DamodaranParserTests.test_rejects_annual_history_that_does_not_end_in_2007 \
  tests.test_backtest_cli.RefreshTransactionTests.test_stale_signal_tail_fails_instead_of_publishing_a_longer_bundle \
  tests.test_backtest_cli.RefreshTransactionTests.test_internal_signal_gap_fails_instead_of_disabling_the_veto

FAILED (failures=5)
```

Both annual-ending subcases, the stale signal tail, and both DGS10/DFII10 internal-gap subcases failed because the old behavior accepted them.

### Final gate GREEN and data evidence

```text
.venv\Scripts\python.exe -m unittest tests.test_damodaran tests.test_backtest_cli
Ran 37 tests in 94.209s
OK

.venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 162 tests in 161.370s
OK

.venv\Scripts\python.exe -m pip check
No broken requirements found.

.venv\Scripts\python.exe -m compileall -q perpetual_engine tests
exit 0

rg -n "[T]BD|[T]ODO|implement later|MSCI World history" perpetual_engine tests config README.md
no matches
```

The rebuilt official-shaped data contain 571 contiguous allocation months from 1979-01-31 through 2026-07-31. `expected_through` and `actual_end` are both 2026-07-31; Treasury is present in every row; TIPS first appears in 2003-02-28 and is present in every required row thereafter. A separate recent-tail run published matching manifest/bundle ends at 2026-06-30; the same tail exceeded 45 days at the later retrieval date and failed closed.

## Fix round 2 — funding regimes and strict bundle end

The Critical and Important review findings were corrected with observable RED tests before production changes:

- Funding rows are filtered before `FundingRate` construction using the exact Task 6 regimes: DFF through 1985-12-31, `USD1MTD156N` from 1986-01-01 through 2021-08-31, and SOFR from 2021-09-01. Real observations outside their source regime are ignored and never reach Task 6.
- Every funding segment intersecting the required study period must be non-empty. Filtered dates must be unique, and the normal Task 6 daily selection enforces exact boundary availability and the seven-calendar-day carry limit.
- `expected_through` is the last complete month before `retrieved_at`, limited by actual World/FX, defensive, and continuous funding outcome coverage. The shipped config explicitly fixes `max_bundle_staleness_days` at 45.
- The bundle loop now constructs every month from January 1979 through `expected_through`. There is no exception-driven truncation: a Task 3–6 error, missing month, stale rate, funding gap, or missing boundary observation propagates and aborts publication.
- The published manifest and `BACKTEST_BUNDLE_V1` both record `expected_through` and `actual_end`; preflight requires all four values to equal the actual final calendar month before calculation.

### Fix round 2 RED evidence

```text
.venv\Scripts\python.exe -m pytest \
  tests/test_backtest_cli.py::RefreshTransactionTests::test_official_shaped_refresh_directly_feeds_offline_backtest \
  tests/test_backtest_cli.py::RefreshTransactionTests::test_missing_funding_boundary_fails_instead_of_truncating_bundle \
  tests/test_backtest_cli.py::RefreshTransactionTests::test_internal_funding_gap_fails_instead_of_becoming_bundle_end \
  tests/test_backtest_cli.py::RefreshTransactionTests::test_stale_short_bundle_fails_instead_of_publishing_january_1979 -q
4 failed
```

The valid fixture failed because out-of-regime DFF/SOFR rows were passed to Task 6 and collided with applicable dates. The other three tests failed because no exception was raised: boundary/gap errors were swallowed and the January 1979 short bundle was published despite an August 2026 retrieval.

A separate preflight RED changed the bundle's declared end, updated its artifact hash and vintage, and showed calculation was still reached. GREEN now rejects the manifest/bundle end mismatch before `run_backtest`.

### Fix round 2 GREEN evidence

```text
.venv\Scripts\python.exe -m pytest tests/test_backtest_cli.py tests/test_refresh_parsers.py -q
23 passed in 75.10s

.venv\Scripts\python.exe -m pytest -q
158 passed in 40.35s

.venv\Scripts\python.exe -m pip check
No broken requirements found.

.venv\Scripts\python.exe -m compileall -q perpetual_engine tests
exit 0

rg -n "[T]BD|[T]ODO|implement later|MSCI World history" perpetual_engine tests config README.md
no matches

rg -n "urlopen|requests\.|httpx|yfinance" perpetual_engine tests
one production match in the sole data_sources adapter; remaining matches are network-isolation tests

.venv\Scripts\python.exe -m json.tool config\data_sources_v1.json
.venv\Scripts\python.exe -m json.tool config\backtest_v1.json
both exit 0
```

The official-shaped fixture includes harmless DFF observations after 1985 and SOFR observations before September 2021, yet refresh succeeds through 2026-07-31 using only the applicable source in each regime. Removing the 1986-01-01 LIBOR boundary or one internal DFF fixing fails closed and leaves no pointer.

## Fix round 1 — binding review corrections

All two Critical and five Important findings were corrected with tests first:

- Every required parser kind in the shipped inventory is now executable offline: exact Task 4 French archives, WDI `USA/WLD CM.MKT.LCAP.CD`, source-configured FRED CSV, Eurostat Italian HICP JSON, and the existing Damodaran parsers. Each emits validated `ObservationRow` CSV with frozen URL, retrieval timestamp, and raw hash provenance.
- Refresh now calls the existing Task 3–6 World-splice, FX, ERP/signal, defensive, CPI-splice, and leveraged builders, truncates at the last complete common month, and publishes a real `BACKTEST_BUNDLE_V1` through `current_manifest.json`. The official-shaped offline E2E fixture runs refresh then backtest and produces the exact five strategies.
- Pointer-replacement failure removes the unpointed new vintage while preserving the prior pointer and vintage.
- Recommendation status now requires World `PASS`, LWLD `PASS_LWLD_VALIDATION`, and either licensed daily `PASS_FULL_DAILY` or official-summary `PASS_PARTIAL_OFFICIAL_SUMMARY` benchmark evidence.
- Every derived artifact has an explicit role and role-specific allowed schema. Preflight checks them all and preserves role/schema in `run_manifest.json`.
- `vintage_id` is a deterministic SHA-256 binding over config hash, sorted source URLs/raw hashes/parser versions, and sorted derived paths/hashes/roles/schemas. Offline preflight recomputes it, so changing bytes plus the declared artifact hash under an old vintage fails closed.
- Output path validation applies the existing Windows basename rules to every segment, rejects trailing dots/spaces and ADS/device names, and detects case-folded duplicates plus file/directory ancestor collisions before calculation or output creation. Reproducibility compares complete recursive relative paths and bytes.

### Fix round 1 RED evidence

The parser and official workflow tests were added before implementation:

```text
.venv\Scripts\python.exe -m pytest tests\test_refresh_parsers.py tests\test_backtest_cli.py::RefreshTransactionTests::test_official_shaped_refresh_directly_feeds_offline_backtest -q
ERROR: ImportError: cannot import name 'parse_eurostat_hicp_json'
```

After the parser GREEN step, the real builder integration exposed and fixed two fixture-boundary failures in order: the missing December 1976 prior FX fixing, then the lack of a pre-1979 ERP observation. No production shortcut or second calculator was introduced.

The C–G contract tests were then added before their implementation:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest_cli.py -q
ERROR: ImportError: cannot import name '_recommendation_status'
```

The tests cover pointer publication failure injection, all gate combinations, missing/unknown schema on a non-bundle derived artifact, stale vintage after updating a mutated artifact hash, Windows device/ADS/trailing-name rejection, ancestor and case-fold collisions, and recursive output identity.

### Fix round 1 GREEN evidence

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest_cli.py tests\test_refresh_parsers.py -q
19 passed in 4.83s

.venv\Scripts\python.exe -m pytest -q
154 passed in 8.75s

.venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 154 tests in 5.438s
OK

.venv\Scripts\python.exe -m pip check
No broken requirements found.

.venv\Scripts\python.exe -m compileall -q perpetual_engine tests
exit 0

rg -n "[T]BD|[T]ODO|implement later|MSCI World history" perpetual_engine tests config README.md
no matches

rg -n "urlopen|requests\.|httpx|yfinance" perpetual_engine tests
one production network call: perpetual_engine/data_sources.py (the sole adapter); all other matches are isolation tests

.venv\Scripts\python.exe -m json.tool config\data_sources_v1.json
.venv\Scripts\python.exe -m json.tool config\backtest_v1.json
both exit 0
```

No test made a real network request. No dependency, plugin, or GDI code was added.

## Scope delivered

- Preserved `evaluate` and added exactly `data refresh --config ...` and `backtest --config ... --output ...`.
- Added one network adapter, `data_sources.fetch_url()`. It is the only production occurrence of `urllib.request.urlopen`; the offline loader, parsers, calculator, and report writers do not call the network.
- Added a staged refresh transaction: all sources download before required parsing, raw bytes use `freeze_bytes`, normalized rows use `ObservationRow` CSV, and `current_manifest.json` is atomically replaced only after the required set succeeds.
- Added explicit source inventory fields for source ID, official URL/endpoint, parser kind/version, required versus validation-only status, normalized path, and output data root.
- Added a frozen `BACKTEST_INPUT_MANIFEST_V1` / `BACKTEST_BUNDLE_V1` loader. It verifies schema, raw and derived hashes, safe unique paths, timestamps, vintage identity, validation statuses, exact five strategies, Task 7 costs/fees, and the complete calendar before creating an output directory.
- Constructed real `SignalSnapshot` and `LeveragedReturn` records and called Task 7 `run_backtest`; no second portfolio calculator exists.
- Added atomic, sorted, UTF-8 deterministic output generation for the eight required files. JSON uses canonical sorted serialization and CSV uses an explicit LF terminator.
- Added a self-excluding `run_manifest.json` with config/input hashes, all raw/derived input hashes, package version, retrieval/run-as-of/vintage identity, cost/fee scenario, nominal/pre-tax labels, validation statuses, recommendation status, and every other output hash.
- Missing official World and LWLD NAV evidence remains `FAIL_MISSING_OFFICIAL_NAV` / `FAIL_MISSING_OFFICIAL_LWLD_NAV`; failed evidence keeps backtest outputs and sets recommendations to `UNVALIDATED`.
- Replaced the README's obsolete “no historical backtest” statement with the full refresh/offline workflow, output meanings, labels, evidence failures, pre-tax boundary, and no-live-trading warning.

## RED evidence

The complete Task 8 test module was written before production implementation:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest_cli.py -q
ERROR tests/test_backtest_cli.py
ModuleNotFoundError: No module named 'perpetual_engine.backtest_report'
```

After the first minimal GREEN implementation, a recommendation-gate test was added before its manifest field:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest_cli.py::OfflineBacktestTests::test_two_runs_are_byte_identical_and_manifest_hashes_reconcile -q
1 failed
KeyError: 'recommendation_status'
```

The first focused implementation run also caught one integration error: `backtest_report` had imported the adapter by value, so patching the designated `data_sources.fetch_url` boundary did not intercept it. The report now calls the adapter through its defining module.

A final preflight audit added a non-contiguous-bundle test before duplicating Task 7's calendar guard at the loader boundary. RED showed that a patched calculator was called and report serialization later failed; GREEN now rejects the calendar before `run_backtest` is invoked or an output path is created.

## GREEN evidence

Task 8 focused pytest checkpoint:

```text
.venv\Scripts\python.exe -m pytest tests\test_backtest_cli.py -q
10 passed in 2.43s
```

Final CLI-focused unittest checkpoint, including the pre-existing `evaluate` tests:

```text
.venv\Scripts\python.exe -m unittest tests.test_cli tests.test_backtest_cli -v
Ran 12 tests in 1.715s
OK
```

Final complete-suite checkpoint:

```text
.venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 145 tests in 3.886s
OK
```

Dependency, compilation, configuration, and scan checkpoints:

```text
.venv\Scripts\python.exe -m pip check
No broken requirements found.

.venv\Scripts\python.exe -m compileall -q perpetual_engine tests
exit 0

rg -n "[T]BD|[T]ODO|implement later|MSCI World history" perpetual_engine tests config README.md
NO_PLACEHOLDERS_OR_MISLABELS

rg -n "urlopen|requests\.|httpx|yfinance" perpetual_engine tests
one production match: perpetual_engine/data_sources.py:40

.venv\Scripts\python.exe -m json.tool config\data_sources_v1.json
.venv\Scripts\python.exe -m json.tool config\backtest_v1.json
both exit 0
```

No test performed a real network request.

## Tests added

1. New CLI grammar plus existing `evaluate` compatibility.
2. Offline backtest success while `urllib.request.urlopen` raises.
3. Refresh access through the sole network adapter.
4. Multi-source required failure preserving the published pointer and vintage bytes.
5. Raw/derived hash corruption and a non-contiguous calendar failing before calculator invocation and output creation.
6. Real Task 7 backtest producing all eight named artifacts.
7. Byte-identical relative file sets and bytes across two output directories.
8. Output-hash reconciliation, manifest self-hash exclusion, frozen run timestamp, and explicit unvalidated recommendation.
9. Named missing official World/LWLD NAV failures.
10. Malformed schema, path traversal, duplicate artifact paths, duplicate output paths, and traversing output paths failing closed.

## Files changed

- `perpetual_engine/backtest_report.py` — refresh transaction, real Task 3–6 bundle construction, deterministic vintage identity, frozen-bundle preflight/loader, Task 7 orchestration, recommendation gate, safe paths, and deterministic report package.
- `perpetual_engine/data_sources.py` — sole HTTPS byte-download adapter plus audited WDI, FRED, and Eurostat offline parsers.
- `perpetual_engine/market_proxy.py` — public exact-French-archive parser reusing the audited Task 4 parsing logic.
- `perpetual_engine/cli.py` — new commands and command-safe error reporting while preserving `evaluate`.
- `tests/test_backtest_cli.py` — Task 8 RED/GREEN CLI, transaction, E2E, preflight, gate, vintage, and path contract tests.
- `tests/test_refresh_parsers.py` — offline required-parser fixtures and fail-closed parser tests.
- `config/data_sources_v1.json` — explicit frozen-data root and official-source inventory.
- `config/backtest_v1.json` — explicit frozen input manifest and output-path contract.
- `README.md` — operator workflow, evidence labels, outputs, limitations, and safety boundary.
- `.superpowers/sdd/2026-08-22-perpetual-engine-historical-backtest-plan/task-8-report.md` — this report.

Final-gate additions also changed `tests/test_damodaran.py` to use a continuous 1977–2007 legacy workbook fixture and assert the exact annual terminus. No configuration value or dependency changed.

No dependency, plugin, GDI component, tax approximation, broker integration, or real-network test was added.

## Explicit assumptions and fail-closed rulings

- The offline input is a separately audited frozen bundle referenced by its manifest; filesystem location is not part of run identity, but manifest/config bytes and every declared artifact hash are.
- Bundle input series use explicit month-keyed arrays so duplicate months remain detectable; mappings are constructed only after uniqueness checks.
- The bundle stores Task 4/6 validation evidence as deterministic JSON. Missing official NAV payloads are converted only to their binding named failure states, never to passing evidence.
- Every required source in the shipped inventory has an audited parser and is used to build the frozen backtest bundle. Only explicitly validation-only official evidence may record a named unavailable state.
- Output directories are immutable run packages: an existing output directory is rejected rather than overwritten. Publication uses a sibling staging directory and one rename.
- No wall-clock timestamp enters offline output. The refresh clock is allowed only in the networked command and can be injected in tests for byte-stable fixture evidence.
- The independent review gate remains for the controller/reviewer because this implementation assignment explicitly prohibited subagents.
