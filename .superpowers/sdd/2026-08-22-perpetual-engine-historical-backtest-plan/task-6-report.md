# Task 6 report — Leveraged World proxy and fee layers

## Scope and files

- Added `perpetual_engine/leveraged_proxy.py`.
- Added `tests/test_leveraged_proxy.py`.
- Added this report. `config/data_sources_v1.json` already contained the exact DFF, USD1MTD156N, and SOFR identifiers, so it was not changed.

No portfolio accounting, transaction costs, network access, or claim of exact MSCI/LWLD history was added.

## RED evidence

The new focused test file was written before the production module. Its first run failed during collection as expected:

```text
ModuleNotFoundError: No module named 'perpetual_engine.leveraged_proxy'
```

A second test-first regression established that a SOFR observation dated before 2021-09-01 could incorrectly be carried into the SOFR regime. The focused run failed because no `ValueError` was raised. The implementation now rejects every funding row whose series does not match its own observation-date regime.

A final RED case added an unmatched proxy day beside an otherwise complete licensed-daily sample. It initially returned `PASS_FULL_DAILY`; the full-daily branch now requires the proxy dates, official dates, and caller-supplied expected dates to match exactly.

## GREEN evidence

Fresh offline checks:

```text
.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py -q
8 passed

.venv\Scripts\python.exe -m pytest -q
104 passed
```

## Implemented behavior

- Frozen `FundingRate`, `LeveragedReturn`, and `LeveragedValidation` records preserve the underlying, funding, residual-drag, FX, and TER effects on separate ledger fields.
- DFF is accepted through 1985-12-31, USD1MTD156N from 1986-01-01 through 2021-08-31, and SOFR from 2021-09-01. Funding uses annual percentages, actual calendar days/360, seven-day maximum same-series carry, and fails closed for absent, non-finite, duplicate, or wrong-regime rates.
- Daily 2x return resets each interval; FX is applied once after USD leverage. The optional 0.60% investable fee accrues by actual calendar days/365. Daily rows never apply the monthly residual.
- Monthly long-history proxy accrues each calendar day, uses only residual values 0.006/0.011/0.015, keeps its primary 1.1% residual distinct from TER, clamps gross wipeout to -100%, and preserves wipeout until `reset=True`.
- Licensed-daily benchmark, official-summary, and LWLD product validation modes are independent. Daily requires the caller-supplied complete expected-date sample before it can return `PASS_FULL_DAILY`; official summaries can only return `PASS_PARTIAL_OFFICIAL_SUMMARY`; LWLD uses fee-adjusted product evidence and returns the named missing-NAV or threshold-failure statuses.

## Assumptions and boundaries

- The task consumes normalized, offline `FundingRate` rows. Conversion from frozen `ObservationRow` source artifacts remains the upstream ingestion responsibility; no live source is contacted by this module or its tests.
- A caller carries `LeveragedReturn.wiped_out` into the following monthly call and uses the explicit `reset=True` flag only for a new-capital/reset event. This preserves state without introducing Task 7 portfolio accounting.
- Full daily validation deliberately needs `expected_dates`; an arbitrary overlapping pair of series can never be labelled full daily validation. The module reports a supplied sample’s size and metrics but does not assert that public proxy data are exact MSCI or that pre-inception returns are LWLD NAV.

## Fix round 1

### RED evidence

The new adversarial contracts were added before the fix. The focused run failed during collection because `OfficialSummary`, `SummaryHorizons`, `ReturnSeries`, and `SeriesKind` did not exist:

```text
ImportError: cannot import name 'OfficialSummary' from 'perpetual_engine.leveraged_proxy'
```

The missing contract exposed the root cause of the six findings: validation accepted unlabelled mutable mappings, daily completeness used only a caller assertion, horizon checks used an optional empty mapping, and monthly TER reused the calendar-day fee helper.

### GREEN implementation

- Monthly investable TER is now exactly `(1 - 0.006) ** (1 / 12) - 1`, independent of month length; daily TER remains `(1 - 0.006) ** (calendar_days / 365) - 1`.
- Daily return validates `calendar_days` before any arithmetic and rejects zero, negatives, booleans, and non-integers in both benchmark and investable modes.
- `PASS_FULL_DAILY` now requires exactly matching proxy/official/expected dates, at least 252 observations, first common date on or after 2014-02-01, and consecutive calendar dates with no internal gap.
- `SummaryHorizons` is frozen and has mandatory 3/5/10-year fields. `OfficialSummary` is frozen; five common annual returns plus published 3- and 5-year comparisons are required, while 10-year can be `None` only on both sides. Every published horizon is checked at the 2% threshold.
- `ReturnSeries` plus `SeriesKind` make benchmark pre-fee USD, official leveraged daily, fee-adjusted LWLD EUR, and official LWLD EUR NAV non-interchangeable. The validator rejects wrong kinds instead of comparing an invalid fee or currency layer.
- LWLD validation requires a continuous, exact, 12-or-more-month common sample wholly dated from 2025 onward.

Fresh offline verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py -q
10 passed

.venv\Scripts\python.exe -m pytest -q
106 passed
```

## Fix round 2

### RED evidence

Two new adversarial tests reproduced the review findings:

- a complete 252-observation business-day calendar beginning 2014-02-03 failed because the daily validator demanded one-calendar-day adjacency across Friday-to-Monday;
- both insertion orders of `{2020: Decimal(...), "2020": Decimal(...)}` were accepted because `OfficialSummary` coerced keys with `int()` and values with the generic decimal parser.

### GREEN implementation

- `expected_dates` is now the binding observation calendar: it must be date-only, sorted, unique, at least 252 observations, start no earlier than 2014-02-01, and have no adjacent gap above seven calendar days. Proxy and official keys must each exactly equal that calendar. Friday-to-Monday passes; an absent expected trading day and any gap above seven days fail.
- `OfficialSummary` accepts only non-boolean integer years in the plausible 1900–2100 range and only finite `Decimal` annual returns. It rejects invalid/colliding key forms before normalization, so string/float/bool keys and string/float/non-finite values cannot silently become official observations.

Fresh offline verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_leveraged_proxy.py -q
12 passed

.venv\Scripts\python.exe -m pytest -q
108 passed
```
