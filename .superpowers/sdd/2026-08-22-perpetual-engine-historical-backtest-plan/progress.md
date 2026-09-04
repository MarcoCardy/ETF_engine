# SDD ledger — plan: docs/superpowers/plans/2026-08-22-perpetual-engine-historical-backtest-plan.md

Plan SHA-256: `B6E3ECDD24793B070A882C5988216724B958EB1B2AF499C8089FAE46D98C15DD`  
Spec SHA-256: `8B1A01FE5F1F97580C4C9633B1A0A2906891B8E6C22070E6F12E62854169B293`

## Workspace ruling

Ruling: The workspace is not a Git repository, and the approved plan explicitly forbids initializing Git without user authorization. Execute in the current workspace, use this per-plan ledger, fresh test evidence, implementer reports, and read-only task reviews against the named changed files instead of worktree/commit diff packages. Cost if wrong: review cannot rely on commit ranges, so every task report must enumerate changed files and reviewers must inspect those files directly.

## Pre-flight interface scan

| Task | Shares with | Producer → consumer / internal consistency | Finding and ruling |
|---|---|---|---|
| 1 Gross Rule E | existing models/io/policy/funding; Task 7 | renamed gross fields → later backtest fiscal boundary | Clean. Old net names must be rejected, not aliased. |
| 2 PIT + manifests | Tasks 3–6 and 8 | `ObservationRow`, `SourceArtifact`, as-of/freeze functions → every parser/calculator | Clean. This is the single normalized row contract. |
| 3 Damodaran | Tasks 2, 5, 8 | official ERP rows → allocation signals and refresh | Clean. Exact Stern URLs and timing are binding. |
| 4 World + FX | Tasks 2, 6, 7 | public World/EUR returns → leveraged proxy/backtest | Clean. Public label must never become MSCI. |
| 5 Rates + allocation | Tasks 2–4, 6–7 | PIT signals/state → portfolio weights | Clean. Precedence and no-trade use prior accepted final beta. |
| 6 Leveraged proxy | Tasks 2, 4–5, 7 | World/funding/FX → leveraged sleeve returns | Clean. TER/funding/residual drag stay separate. |
| 7 Backtest | Tasks 1, 4–6, 8 | all signals/returns/costs → deterministic comparator ledgers | Clean. No optimization or tax approximation. |
| 8 CLI + reports | Tasks 2–7 | frozen refresh + offline backtest → user-facing artifacts | Clean. Network access exists only in `data refresh`. |

## Task status

- Task 1: complete
- Task 2: complete
- Task 3: complete
- Task 4: complete
- Task 5: complete
- Task 6: complete
- Task 7: complete
- Task 8: pending

## Task 1 review loop

- Initial implementation: controller verified 31/31 tests passing.
- Review: Needs fixes.
- Important finding: `load_config` accepted a payload containing both `protected_monthly_gross` and legacy `protected_monthly_net`; old names must be rejected whenever present.
- Task 1: fix round 1/5 in progress.
- Task 1: fix round 1/5 addressed (1 finding closed, no new breakage).
- Task 1: complete; controller verified focused 7/7 after fix and reviewer approved.

## Task 2 review loop

- Initial implementation: controller verified 42/42 tests passing.
- Independent review: FAIL with four Important findings: corrupted content-addressed raw files were reused without hash verification; raw/manifest collision for a source named `manifest.json`; inconsistent persisted provenance when identical bytes arrive from different URLs; mutable `quality_flags` accepted in a frozen observation row. One Minor tie-breaker test gap was also reported.
- Task 2: fix round 1/5 in progress under TDD; scoped re-review required before approval.
- Task 2: fix rounds 1–5 closed the four original findings plus Windows filename collision variants discovered during scoped re-review.
- Task 2: complete; controller verified 57/57 tests and independent reviewer returned PASS with no Critical/Important findings.

## Rulings

- Ruling: add and pin `xlrd==2.0.2` in Task 3 solely to parse Damodaran's official legacy `.xls` annual workbook. `openpyxl` cannot read `.xls`, and using a generic pandas parser would be a larger dependency surface. Cost if wrong: one additional runtime dependency; removal would make the required annual source unparseable.
- Environment evidence: `xlrd==2.0.2` installed successfully in `.venv` before Task 3 implementation; pinning in `requirements.txt` remains a Task 3 deliverable.

## Task 3 review loop

- Initial implementation: controller verified focused 12/12; implementer reported full 69/69.
- Independent review: FAIL with three Important findings: monthly history could start after September 2008; annual fallback remained possible after the September 2008 handoff; annual internal gaps were not rejected.
- Fix round 1/5: exact September 2008 start, fail-closed monthly-only selection after the switch, and annual gap validation added under TDD.
- Task 3: complete; controller verified focused 15/15, full suite 72/72 reported and independent scoped reviewer returned PASS.

## Task 4 review loop

- Initial implementation: controller verified focused 8/8; implementer reported full 80/80.
- Independent review: FAIL with three Important provenance/PIT/determinism findings and one Minor metric-test gap.
- Fix round 1/5: composite multi-input provenance, month-outcome PIT timing, provenance-preserving FX records, stable FX vintage tie-break and nontrivial metric tests added under TDD.
- A reviewer objection to month-end `available_at` was challenged against spec §10; reviewer withdrew it because month-end 23:59:59 correctly prevents same-month use and enables the next-month decision without an extra one-month delay.
- Task 4: complete; controller verified focused 12/12, implementer reported full 84/84 and independent reviewer returned PASS.

## Task 5 review loop

- Initial implementation: controller verified focused 11/11; implementer reported full 95/95.
- Independent review: FAIL with four Important findings covering IR3TIB month alignment, CPI gaps, linked-HICP PIT availability, and inconsistent HOLD weights after drift.
- Fix round 1/5: exact M-to-M+2 defensive timing, continuous/composite PIT CPI splice, and full pretrade-weight HOLD/trade-delta accounting added under TDD.
- Task 5: complete; controller verified focused 12/12, reviewer verified full 96/96 and returned PASS.

## Task 6 review loop

- Initial implementation: controller verified focused 8/8; implementer reported full 104/104.
- Independent review: FAIL with five Important findings around monthly TER, daily sample sufficiency, summary evidence, LWLD inception, interval validation, plus a weak series-kind contract.
- Fix round 1/5 introduced exact monthly TER, positive integer day counts, typed series kinds, explicit summary horizons, daily/LWLD sample gates; review then found a Critical trading-calendar regression and one Important ambiguous-year-key issue.
- Fix round 2/5 replaced calendar-day continuity with an exact expected trading calendar (weekends allowed, max 7-day gap) and strict integer annual keys under TDD.
- Task 6: complete; controller verified focused 12/12 and independent reviewer verified full 108/108 and returned PASS.

## Task 7 review loop

- Initial implementation: controller verified focused 15/15; implementer reported full 123/123.
- Independent review found four Important reconciliation/audit/determinism issues and two Minor metadata/drift issues.
- Fix round 1/5 added strict Task 6 layer reconciliation, multiplicative EUR decomposition, allocation flags, fixed Decimal precision, drifted fixed beta and scenario metadata; review then found wipeout/clamp and global-rounding issues.
- Fix round 2/5 enforced pre-FX wipeout, preserved funding with a positive clamp correction, and fixed `ROUND_HALF_EVEN`; review then found persistent wipeout sequencing.
- Fix round 3/5 added chronological wipeout/reset validation and auditable `CAPITAL_RESET`; review then found false monthly overlay refinancing and missing leveraged flags.
- Fix round 4/5 added a post-wipeout overlay lock across fixed/tactical strategies and preserved exact Task 6 flags in each row.
- Task 7: complete; controller verified focused Task 6+7 39/39 and independent reviewer verified full 135/135 and returned PASS.

## Task 8 review loop

- Initial implementation and Fix round 1 connected required official-shaped parsers, refresh, bundle, offline backtest and deterministic outputs.
- Review then found two remaining blockers: overlapping funding sources were merged outside their regimes, and post-start build errors could silently truncate the bundle.
- Fix round 2/5 filters DFF/LIBOR/SOFR to their exact regimes before merge and replaces exception-driven truncation with an explicit `expected_through`, 45-day staleness gate and contiguous January 1979 through actual-end build.
- Controller verified focused Task 8/parser tests 23/23 and full suite 158/158. Independent scoped reviewer observed 571 contiguous months through 2026-07-31, exact funding regimes, matching manifest/bundle end fields and returned PASS.
- Final gate review found two Important omissions: DGS10/DFII10 did not limit or fail-close bundle signal coverage, and the Damodaran annual parser did not require the 2007 terminus.
- Final gate fix added deterministic PIT-aware DGS10/DFII10 coverage limits, monthly gap rejection, loop-level non-null guards, and an exact 2007 annual endpoint under TDD. The implementer verified focused Damodaran/Task 8 tests 37/37 and the final full suite 162/162; inspected data remain 571 contiguous months through 2026-07-31 with no missing Treasury and no missing required TIPS. A recent shorter signal tail deterministically published through 2026-06-30, while the same tail failed once older than 45 days. Independent final re-review remains required.
