# SDD ledger — plan: docs/superpowers/plans/2026-08-27-deterministic-ranking-overlay-plan.md

Ruling: the workspace is not a Git repository, so task boundaries are tracked by file inventories and test evidence instead of commits; cost if wrong: rollback is manual rather than commit-based.

Task 1: in progress

Task 1: review found 4 Important and 1 Minor validation gaps; fix round 1 started.

Ruling: all ranking returns are compared in EUR, with source currency and conversion policy frozen; otherwise USD, GBP and EUR series would embed inconsistent FX bets. Cost if wrong: a different base currency can change historical rankings.

Task 1: fix round 1/5 (3 findings addressed, 2 original plus 3 new Important open).

Ruling: LBMA Gold PM is allowed only for gold as `PRICE_ONLY_NO_INCOME_ASSET`, because physical gold has no distributions to reinvest; cost if wrong: it omits ETC custody fees and tracking drag, which remain visible in the separate tradable track.

Task 1: fix round 2/5 (5 addressed or partially addressed, 2 concrete FX/DTEH source defects open).

Task 1: fix round 3/5 (2 addressed, 0 open).
Task 1: complete (review clean; 5 focused tests and full regression suite pass).

Task 2: review found 1 Critical, 2 Important and 1 Minor; fix round 1 started.

Ruling: retrospective `retrieved_at` records when archived bytes were downloaded, while a separate `available_at` determines point-in-time eligibility. Cost if wrong: this assumes market levels were public on their observation date; non-market releases would need provider-specific publication timestamps.

Task 2: fix round 1/5 (source-track and cash addressed; availability integration defect open).

Task 2: fix round 2/5 (availability integration addressed, 0 open).
Task 2: complete (review clean; 18 focused tests and 211 full-suite tests pass).

Task 3: review found 1 Important integrity gap; normalized CSV hashes were not bound into the manifest. Fix round 1 started.

Task 3: fix round 1/5 (normalized-file bindings addressed, 0 open).
Task 3: complete (review clean; 26 focused tests and full suite pass).

Task 4: initial implementation complete; independent review found 2 Important source/as-of integrity gaps and 1 Minor report-completeness gap.
Task 4: fix round 1/5 (monthly SWDA return and survivorship disclosure added).
Task 4: fix round 2/5 (benchmark/cash role, URL, hash, availability, staleness and timezone checks added; raw vintage paths, byte counts and hashes enforced; human report completed).
Task 4: complete (independent re-review clean on load-bearing findings; 30 focused tests pass; outputs are byte-identical across two runs).

Task 5: complete (non-authoring review performed; every load-bearing finding has a covering test; final full suite 223 passed; final monthly table inspected: 78 complete rows, 1 partial row and 1 provisional row).
