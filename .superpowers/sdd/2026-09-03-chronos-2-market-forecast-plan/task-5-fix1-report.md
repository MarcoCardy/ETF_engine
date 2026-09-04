# Task 5 fix round 1 report

Status: Important finding addressed, ready for independent re-review; not yet approved.

## Finding and root cause

`evaluate_chronos()` loaded and analyzed a manifest-validated target table, then recalculated the target CSV and manifest hashes only when building its output manifest. A target mutation during the long evaluation could therefore make the published hashes describe different bytes from those analyzed.

## Minimal root fix

- Added `load_target_snapshot()`, which reads each target artifact once, validates and parses those exact in-memory bytes, and returns their exact SHA-256 hashes with the immutable table.
- Kept `load_target_table()` as a compatibility wrapper so existing consumers retain their interface without duplicated parsing.
- Made evaluation use the snapshot hashes in its manifest and revalidate both files immediately before atomic publication; intervening changes now fail closed without an output directory.

## TDD and verification evidence

- RED: the snapshot interface was absent and a target mutation after loading was published without error (1 error, 1 failure).
- GREEN regression plus forecast compatibility: 7/7 passed.
- Focused Task 5 suite: 6/6 passed in 13.874s.
- Full suite: 259/259 passed in 90.793s.
- Syntax check for `chronos.py`, `chronos_data.py`, and `test_chronos.py`: passed.

## Review package

- Base: `task-5-fix1-base/`
- Head: `task-5-fix1-head/`
- Diff: `task-5-fix1-review.diff`

Independent re-review remains required.
