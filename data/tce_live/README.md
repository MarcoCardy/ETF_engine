# TCE v1.0 prospective shadow ledger

This directory is reserved for the append-only TCE shadow record beginning no earlier than 2026-08-27.

The live ledger has two normalized tables when the first real observation is recorded:

- `events_v1.csv`: `event_id`, candidate, whitelisted event type, published/available/recorded UTC timestamps, source URL and SHA-256, mapper version, and `LIVE_RECEIVED` status.
- `decisions_v1.csv`: decision timestamp and hash, action, prior and resulting candidate, leader, execution date, score hashes, and configuration hash.

Rows are never edited or deleted. A correction is a new event and decision referencing new source bytes. The deterministic event or decision hash is the identity used to detect accidental duplicate appends.

Historical replay data must not be mixed into these live files. Any archived timestamp reconstructed after the fact is labelled `HISTORICAL_TIMESTAMP_RECONSTRUCTED` and belongs to a separately frozen research vintage. It is not prospective evidence.

No empty CSV is committed: headers are created atomically with the first observation, so an empty file cannot be mistaken for a functioning monitor.
