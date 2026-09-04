# Task 6 fix round 4 report

Status: dangling-lock creation and the lstat/open race are fixed; ready for independent re-review.

## Root-cause fix

The lock is now installed as a fully initialized local inode rather than created through the final path:

1. `mkstemp()` creates an owned candidate inside the resolved output root.
2. The candidate receives exactly `b"0"`, is flushed and `fsync()`ed before publication.
3. `os.link(candidate, .publication.lock)` installs it atomically. If any entry, including a dangling symlink, already occupies the destination, hard-link creation fails without following or creating that entry's target.
4. The candidate name is always removed. The installed inode remains after a successful link.
5. The final lock must be a regular non-symlink direct child whose `lstat`, resolved path, `fstat` and followed `stat` identities all agree. Existing content must already equal exactly `b"0"`; it is never initialized or rewritten.
6. Only after validation is the existing `msvcrt` byte lock acquired.

## TDD and verification evidence

- RED dangling-link reproduction: publication raised only after the old `a+b` created the absent external target.
- RED race reproduction: a competing dangling symlink installed exactly at the final publication operation defeated the prior lstat/open solution.
- GREEN lock regressions: dangling target, competing-link race, existing external link and serialization — 4/4 passed in 0.920s.
- GREEN focused: `ChronosMonitoringTests` — 26/26 passed in 3.846s.
- GREEN full: `.venv\Scripts\python.exe -m unittest discover` — 287/287 passed in 89.769s.
- Syntax: `perpetual_engine/chronos.py` and `tests/test_chronos.py` compile successfully.
- Additional invariant check: an invalid existing lock is rejected and remains byte-for-byte unchanged.

## Review package

- Base: `task-6-fix4-base/`
- Head: `task-6-fix4-head/`
- Diff: `task-6-fix4-review.diff`
- Diff SHA-256: `46E137AB73ECC84FD1CA5D12830834A44978469F9AF5DFFF6EEC542BBB73B708`

Independent re-review is required before Task 6 is marked complete.
