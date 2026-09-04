# SDD ledger — plan: docs/superpowers/plans/2026-09-03-chronos-2-market-forecast-plan.md

Baseline: 226 tests passed in 66.143s with `.venv\Scripts\python.exe -m unittest discover -s tests -v`.

Ruling: the project is not a Git repository and Bash is unavailable — execute in the current workspace, do not initialize Git, snapshot each task's files before and after implementation, and use `git diff --no-index` review packages — cost if wrong: rollback and task attribution are less convenient than commit-based isolation, but all user files remain preserved and each task still receives an explicit diff review.

## Pre-flight consistency scan

| Scope | Producer / consumer relationship | Finding |
|---|---|---|
| Task 1 internal | Creates config, `chronos_data.py`, and target tests matching its declared interfaces | Consistent |
| Task 2 internal | Extends Task 1 data module and tests with five source transforms | Consistent |
| Task 3 internal | Builds frozen refresh and loading on Task 2 parsers | Consistent |
| Task 4 internal | Creates forecasting module using Task 1 and Task 3 tables | Consistent |
| Task 5 internal | Extends forecasting module with evaluation and attribution | Consistent |
| Task 6 internal | Extends forecasting module with immutable publication and reconciliation | Consistent |
| Task 7 internal | Adds CLI dispatch and verifies all earlier interfaces | Consistent |
| Tasks 1–2 | Task 2 extends `chronos_data.py` and `tests/test_chronos.py` created by Task 1 | Consistent; append new parser tests and functions |
| Tasks 1–3 | Task 3 consumes `ChronosConfig`/`MonthlyTable` and extends the shared test file | Consistent |
| Tasks 1–4 | Task 4 consumes `ChronosConfig`, `MonthlyTable`, and target loading; extends tests | Consistent |
| Tasks 1–5 | Task 5 consumes config/table contracts and extends tests | Consistent |
| Tasks 1–6 | Task 6 consumes config/target hashes and extends tests | Consistent |
| Tasks 1–7 | Task 7 consumes the config path and extends tests | Consistent |
| Tasks 2–3 | Task 3 consumes all Task 2 parsers and normalizer | Consistent |
| Tasks 2–4 | Task 4 consumes the ordered normalized covariate table | Consistent |
| Tasks 2–5 | Task 5 derives dynamic ablations from configured covariates | Consistent |
| Tasks 2–6 | Task 6 indirectly consumes normalized data via scenario forecasts | Consistent |
| Tasks 2–7 | Both append isolated test classes to `tests/test_chronos.py` | Consistent |
| Tasks 3–4 | Task 4 consumes `load_covariate_table()` and vintage ID | Consistent |
| Tasks 3–5 | Task 5 consumes frozen tables and hashes | Consistent |
| Tasks 3–6 | Task 6 binds forecast identity to the vintage ID | Consistent |
| Tasks 3–7 | Task 7 dispatches the refresh function and extends tests | Consistent |
| Tasks 4–5 | Task 5 consumes model adapter and forecast-input construction | Consistent |
| Tasks 4–6 | Task 6 consumes scenario forecasts and `ForecastRow` | Consistent |
| Tasks 4–7 | Task 7 dispatches publication and uses the shared test file | Consistent |
| Tasks 5–6 | Both extend `chronos.py`; Task 6 does not change evaluation interfaces | Consistent |
| Tasks 5–7 | Task 7 dispatches `evaluate_chronos()` and extends tests | Consistent |
| Tasks 6–7 | Task 7 dispatches publication/reconciliation and extends tests | Consistent |

Pre-flight result: no plan/spec conflict beyond the recorded no-Git execution ruling.

Task 1: review 1 — spec non-compliant, quality needs fixes.
Task 1: minor (deferred): `load_target_table()` lets a missing target propagate `FileNotFoundError`; final review must decide whether the CLI's existing `OSError` boundary is sufficient.
Task 1: reviewer could not verify future-task behavior (`local_files_only`, Treasury units, future-covariate filtering, output shape, offline CLI); these are assigned to Tasks 2, 4, and 7 and are not Task 1 gaps.
Task 1: fix round 1/5 (2 addressed, 0 open; snapshots task-1-head..task-1-fix1-head).
Task 1: complete (snapshots task-1-base..task-1-fix1-head, review clean).

Mid-plan ruling: user-approved requirements added US headline CPI, explicit rate-scenario sensitivity, realized-volatility diagnostics, and a separate investable portfolio monitor. Task 2 was paused before implementation and revised for CPI; its implementation now includes the fifth source. Volatility remains a diagnostic rather than a duplicate predictor. Before Task 8 completed, the user superseded the earlier QDVA/QDVB/DBMG candidate with `P_WORLD_FACTOR_TREND = 60% SWDA + 15% IWMO + 15% IWQU + 10% DBMFE`; it maps directly to `WORLD`, `MOMENTUM`, `QUALITY`, and `TREND`, uses four EUR listings without FX, and starts only at the first real common observation because DBMFE has no synthetic pre-launch history. Chronos' longer targets remain factor proxies and are labelled accordingly.

Task 2: review 1 found duplicate dates could pass with different hashes and config accepted unsupported extra past-only sources.
Task 2: fix round 1/5 resolved both findings; focused 15/15 and full suite 241/241 passed (snapshots task-2-head..task-2-fix1-head).
Task 2: minor deferred: no direct malformed-number/duplicate-date raw FRED CSV regression, while the shared parser already enforces both and the five-source normalized duplicate regression is present.
Task 2: complete (review clean on all Important findings).

Task 3: implementation complete; focused 4/4 and full suite 245/245 passed (snapshots task-3-base..task-3-head, review package task-3-review.diff); review pending.
Task 3: review 1 found unbound retrieval provenance/raw metadata and permissive staging-path containment.
Task 3: fix round 1/5 addressed both findings; three regression tests RED then GREEN and focused refresh suite 7/7 passed (snapshots task-3-head..task-3-fix1-head); re-review pending.
Task 3: complete (fix1 re-review clean; no open Important findings).
Task 4: complete; focused 5/5 and full suite 253/253 passed, independent review found no Important issues (snapshots task-4-base..task-4-head, review package task-4-review.diff).
Task 4: minor deferred: add explicit negative-rate no-floor and distinct scenario/target/horizon fake-output mapping tests if the adapter is later changed; current implementation was reviewed as correct.

Task 5: implementation complete; TDD RED captured for missing interfaces, staged-hash validation, and ancestor collision; focused 4/4 and final full suite 257/257 passed (snapshots task-5-base..task-5-head, review package task-5-review.diff); independent review pending.
Task 5: review 1 found target hashes could be recalculated from bytes different from the target snapshot actually analyzed.
Task 5: fix round 1/5 addressed the finding with a single-read target snapshot plus pre-publication mutation check; two regressions RED then GREEN, focused 6/6 and full suite 259/259 passed (snapshots task-5-fix1-base..task-5-fix1-head, review package task-5-fix1-review.diff); independent re-review pending.
Task 5: complete; fix1 manually re-reviewed after the independent reviewer exhausted its external usage allowance. The root review found no remaining Important issue: the table and hashes derive from the same in-memory bytes, the manifest records those hashes, pre-publication mutations fail closed, and the focused 6/6 suite was re-run successfully.
Task 6: implementation complete; TDD RED captured for all missing public interfaces and for a non-flat `ECB_FLAT` manifest path, focused monitoring suite 6/6 and full suite 265/265 passed (snapshots task-6-base..task-6-head, review package task-6-review.diff); independent review pending.
Task 6: review 1 found seven Important issues: unbound inference reloads, archive hash/parse TOCTOU, link/junction escapes, unanchored `issued_at`, ECB curves not bound to archived vintage, pre-replace input-mutation windows, and a forecast left behind after automatic reconciliation failure; two local minors covered frozen model metadata and extra CSV fields.
Task 6: fix round 1/5 addressed all findings with dedicated RED regressions; focused monitoring 17/17, adjacent Chronos 34/34, and full suite 276/276 passed (snapshots task-6-fix1-base..task-6-fix1-head, review package task-6-fix1-review.diff); independent re-review pending.
Task 6: fix round 2/5 addressed five remaining Important findings with seven dedicated RED regressions: atomic publication ownership plus Windows output-root locking and contained rollback, private bootstrap proof with the exact three-argument public reconciliation interface, real direct-child monitoring anchors, same-byte vintage/pointer validation, and stable double-read target bundles at the commit point. Focused Refresh+Monitoring 31/31 and full suite 283/283 passed (snapshots task-6-fix2-base..task-6-fix2-head, review package task-6-fix2-review.diff); independent re-review pending.
Task 6: fix round 3/5 addressed the two concrete path escapes with dedicated RED regressions: publication lock links cannot escape/write outside the output root, and the current-vintage pointer must resolve inside `data_root`. The module-global bootstrap token was removed as a false security signal; the exact public three-argument interface remains non-bypassable, while underscore internals and writable artifact storage are explicitly the trusted application boundary. Focused Refresh+Monitoring 33/33 and full suite 285/285 passed (snapshots task-6-fix3-base..task-6-fix3-head, review package task-6-fix3-review.diff); independent re-review pending.
Task 6: fix round 4/5 closed the dangling-lock and lstat/open race with an initialized internal candidate installed atomically through `os.link`; existing entries are never followed, initialized, or rewritten, and path/handle identity is validated before the `msvcrt` byte lock. Dedicated dangling and competing-link regressions went RED then GREEN; focused Monitoring 26/26 and full suite 287/287 passed (snapshots task-6-fix4-base..task-6-fix4-head, review package task-6-fix4-review.diff); independent re-review pending.
Task 6: complete; fix4 independent re-review found no Important issues. The reviewer re-ran 4/4 lock tests and 26/26 monitoring tests, verified Windows inter-process serialization plus link/junction/hardlink/temp-cleanup probes, and confirmed all earlier Task 6 findings remain closed.
Task 7: complete; TDD captured five expected parser errors, then focused CLI 2/2, Chronos 63/63, and full suite 289/289 passed. `pip check` was clean, requirements pins were unchanged, and the cached offline CPU smoke returned one finite `(4, 12, 3)` output. The independent reviewer exhausted its usage allowance; root manually reviewed the 7.5 KB diff, found no Important issue, and re-ran the focused CLI 2/2 successfully.
Task 7: implementation complete; TDD RED captured five parser errors before `chronos` existed, then focused CLI 2/2, all Chronos 63/63, and full suite 289/289 passed. Pip check is clean, required pins are unchanged, lazy imports expose exactly four commands, and the cached offline CPU smoke returned one finite `(4, 12, 3)` output (snapshots task-7-base..task-7-head, review package task-7-review.diff); independent review pending.
