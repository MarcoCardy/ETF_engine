# Task 3 report — Frozen five-covariate refresh transaction

## Result

- Added transactional refresh for ECB DFR, DGS10, Brent, BIS global-liquidity proxy, and US headline CPI.
- Each raw file, normalized CSV, availability rule, configuration hash, and source identity is bound into an immutable vintage ID and manifest.
- Publication uses a staging child, validates it before publication, moves the completed vintage into place, and replaces `current_manifest.json` last.
- Failed parsing, fifth-source retrieval, pointer replacement, corrupt hashes, bad schemas, non-finite values, or path escape fail closed.
- Offline loading verifies the pointer, immutable identity, every raw/generated hash, exact five-column order, and read-only finite arrays.

## TDD evidence

- RED: three expected import failures for the missing refresh/loader interfaces were captured before implementation.
- Focused GREEN: `ChronosRefreshTests` — 4/4 passed.
- Full suite: 245 tests passed in 158.470 seconds.

## Packaging

- Base snapshot: `task-3-base/`
- Head snapshot: `task-3-head/`
- Review diff: `task-3-review.diff`
- Commits: none; the project is not a Git repository and none was initialized.

## Concerns

Independent review found two Important gaps: retrieval-dependent raw provenance was not part of the immutable identity, and staging containment relied on a parent directory name instead of the resolved configured root.

Fix round 1 added the complete raw payload/provenance hash inventory to the manifest and vintage identity, made distinct retrieval timestamps distinct vintages, rejected corrupt or extra raw metadata, and required resolved `.staging` and `vintages` directories to remain below the configured data root. Three regression tests failed before the change and now pass; all seven `ChronosRefreshTests` pass.

Minor deferred: the pointer-failure test replaces the helper rather than inducing an operating-system `Path.replace()` failure. The helper is separately responsible for deleting its temporary file, and the transactional behavior is already asserted at its caller boundary.
