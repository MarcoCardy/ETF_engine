# Task 5 report — Rates, CPI, defensive returns, and allocation

## Result

Implemented the offline Task 5 allocation engine and its source-specific PIT helpers. No network call, optimizer, borrowing facility, leveraged-proxy return, fee, or funding-accrual implementation was added; the three future funding source IDs are metadata only for Task 6.

## RED evidence

`tests/test_allocation.py` was written before `perpetual_engine/allocation.py` existed. Focused test execution failed during collection with:

```text
ModuleNotFoundError: No module named 'perpetual_engine.allocation'
```

That failure established the missing public Task 5 API rather than a test typo. The test file covers raw-beta boundaries; Crisis/Recovery precedence and transitions; TIPS caps and 36-observation eligibility; strict no-trade boundary; source timing; CPI splice; prior-only signal construction; and allocation invariants.

## GREEN evidence

Fresh final verification:

```text
.venv\\Scripts\\python.exe -m pytest tests/test_allocation.py -q
11 passed

.venv\\Scripts\\python.exe -m pytest -q
95 passed
```

The workspace is intentionally not a Git repository, so Git diff verification is unavailable. The commands above exited successfully; the repository-wide suite is the available integration check.

## Changed files

- `perpetual_engine/allocation.py` — immutable signal/allocation ledger rows; PIT-only signal calculation; prior-only expanding midranks; state precedence, band, caps, floor/no-borrowing targets; CPI splice; defensive return.
- `perpetual_engine/data_sources.py` — exact DGS10/DFII10 next-US-federal-business-day and IR3TIB M+1 fallback helpers plus normalization dispatcher.
- `config/data_sources_v1.json` — Task 5 rate/inflation source identifiers, fallback metadata, exact HICP endpoint, and Task 6 funding metadata marker.
- `tests/test_allocation.py` — 11 deterministic, no-network contract tests.
- `task-5-report.md` — this evidence record.

## Assumptions and boundaries

- All upstream rate rows are normalized to annual decimal values before defensive-return calculation; `defensive_monthly_return()` requires the caller to supply the explicit staleness limit.
- A `HOLD` records zero tactical trades and retains the caller-supplied, drifted pre-trade sleeve weights. If the core weight changed, the complete pre-trade record is mandatory so the ledger does not invent a drift state.
- A month’s decision timestamp is the final instant of that preceding outcome month; therefore that completed month’s admissible market/rate rows can inform the next allocation, while rows dated in the allocation month are excluded.
- `DFII10` has no cap until the snapshot has at least 36 strictly prior monthly means. No synthetic pre-2003 series is created.

## Fix round 1 — four Important findings

### RED evidence

Added four regression contracts before the fix and ran the focused suite. It failed in exactly the expected places:

- a January rate was allowed to be queried for a non-immediate March outcome instead of rejecting the requested allocation month first;
- a linked December HICP row exposed its own December 5 availability rather than the December 10 OECD-November link availability;
- `next_allocation()` did not accept or validate a full pre-trade sleeve record and allowed a drifted-core `HOLD` without one;
- the API did not accept pre-trade sleeve weights needed to record signed trade deltas.

### GREEN implementation

- `defensive_monthly_return()` now rejects any `allocation_month` other than the calendar month immediately after `decision_at`, before selecting a rate. January/FEB/March boundary coverage proves a January `IR3TIB` fallback can only price March from the February decision.
- CPI splicing now rejects duplicate monthly observations and every internal calendar gap from the first OECD month through the last HICP month, including a missing December 2023. Linked HICP rows use the maximum availability/retrieval timestamp of HICP and the OECD-November link, plus a deterministic composite provenance hash; the new as-of test excludes December before that link exists.
- `next_allocation()` accepts `overlay_weight_pretrade` and `defensive_weight_pretrade`, validates non-negative finite complete weights summing with core within `1e-12`, fails closed on a changed-core `HOLD` without them, preserves supplied drifted weights/actual beta on `HOLD`, and records signed sleeve deltas on `TRADE`. Inception continues to use zero overlay/defensive pre-trade sleeves.

Fresh focused checkpoint after the fix:

```text
.venv\\Scripts\\python.exe -m pytest tests/test_allocation.py -q
12 passed

.venv\\Scripts\\python.exe -m pytest -q
96 passed
```
