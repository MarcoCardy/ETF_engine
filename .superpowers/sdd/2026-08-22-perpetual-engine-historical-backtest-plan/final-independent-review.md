# Final independent review — Phase 2 historical backtest

Date: 2026-08-26  
Reviewer role: independent, read-only implementation gate  
Verdict: **PASS**

No Critical or Important findings remain.

## Scope reviewed

- `docs/superpowers/specs/2026-08-21-perpetual-engine-historical-backtest-design.md`
- `docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md`
- `.superpowers/sdd/2026-08-22-perpetual-engine-historical-backtest-plan/progress.md`
- Task 1–8 briefs and implementation reports
- `perpetual_engine/*.py`, `tests/*.py`, `config/*.json`, `README.md`, and `requirements.txt`
- frozen-manifest, bundle, validation-status, and deterministic-output paths

## Re-review of the two prior findings

1. **DGS10/DFII10 coverage and staleness — closed.**
   - `perpetual_engine/backtest_report.py:283-297` validates continuous monthly signal coverage using only rows available by the applicable month-end and returns the final allocation month supported by each source.
   - `perpetual_engine/backtest_report.py:447-467` includes DGS10 and, from February 2003, DFII10 in the explicit common `expected_through` limit and applies the 45-day bundle staleness gate.
   - `perpetual_engine/backtest_report.py:471-486` requires Treasury for every allocation month and TIPS from February 2003; a missing signal aborts publication instead of silently disabling the veto.
   - Adversarial tests cover stale signal tails, a recent signal-limited final month, and internal gaps in both DGS10 and DFII10.

2. **Damodaran annual handoff — closed.**
   - `perpetual_engine/data_sources.py:428-474` requires a continuous annual history ending exactly in 2007.
   - `tests/test_damodaran.py:158-191` rejects truncated histories ending in 1978 or 2006.
   - The official-shaped end-to-end fixture now contains the complete 1977–2007 segment.

## Regression checks

- Funding inputs remain filtered before merging:
  - DFF: 1979-01-01 through 1985-12-31
  - USD1MTD156N: 1986-01-01 through 2021-08-31
  - SOFR: 2021-09-01 through 2026-07-31 in the inspected fixture
- Missing funding boundaries and internal funding gaps still abort refresh.
- Manifest and bundle both require `expected_through == actual_end ==` the actual final calendar month before calculation.
- Post-start build errors are not caught and converted into a shorter published history.
- Source-domain restrictions, point-in-time joins, gross-fiscal Rule E, allocation precedence/core drift, leveraged reset/funding/TER accounting, exact five strategies, offline backtest isolation, reproducibility, and named World/LWLD validation failures remain covered and passing.

## Executed verification

- Focused Damodaran, signal, funding, staleness, and refresh/backtest tests: **23 passed in 168.33s**.
- Complete suite: **162 passed in 170.39s**.
- `.venv\Scripts\python.exe -m pip check`: **No broken requirements found**.
- `.venv\Scripts\python.exe -m compileall -q perpetual_engine tests`: exit 0.
- Placeholder/mislabel scan: no matches.
- Network scan: one production `urlopen`, confined to `perpetual_engine/data_sources.py`; other matches are isolation tests.

## Resulting-data inspection

The official-shaped refresh-to-offline-backtest fixture produced:

- 571 contiguous months, January 1979 through July 2026;
- manifest and bundle end fields all equal to `2026-07-31`;
- zero missing Treasury signals;
- zero missing TIPS signals from February 2003;
- July 2026 TIPS history count: 281 months;
- exact DFF/LIBOR/SOFR regimes listed above;
- five strategies and 2,855 equity-curve rows;
- named validation states `FAIL_MISSING_OFFICIAL_NAV` and `FAIL_MISSING_OFFICIAL_LWLD_NAV`, with recommendations remaining unvalidated rather than promoted to PASS.

## Remaining issues

None at Critical or Important severity within the approved Phase 2 scope. Missing official World/LWLD NAV evidence remains an explicit validation limitation, not a silent success.
