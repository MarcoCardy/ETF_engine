# Task 8 brief — Refresh, offline CLI, outputs, and historical gate preparation

## Scope/files

Implement only Task 8 under TDD.

- Modify `perpetual_engine/cli.py`.
- Create `perpetual_engine/backtest_report.py`, `tests/test_backtest_cli.py`.
- Modify `README.md` and only the two v1 config files as needed for explicit paths/source inventory.
- Create `task-8-report.md` in this ledger directory.

## Commands

Preserve existing `evaluate`. Add exactly:

```text
python -m perpetual_engine data refresh --config config/data_sources_v1.json
python -m perpetual_engine backtest --config config/backtest_v1.json --output outputs/backtest_v1
```

Return 0 on success, 2 on malformed/failed closed input, and never emit success artifacts after failure.

## Network isolation and refresh transaction

- `data refresh` is the only code path allowed to call the network. Put all `urllib.request.urlopen` use behind one small adapter function in `data_sources.py` or `backtest_report.py`; no parser/report/backtest calls it directly.
- Tests patch that adapter/urlopen: offline backtest must succeed when all network calls raise; refresh must show that only the adapter calls it.
- Config contains explicit source IDs, official URLs/endpoints, parser kind, required/validation-only status and an output data root. Do not infer providers or use unofficial mirrors.
- Download all required bytes first into a staging/vintage directory, compute SHA-256 and parse/validate there. Only after every required source succeeds, atomically publish a deterministic `current_manifest.json` pointer/manifest. A failure must leave the prior published pointer and prior vintage bytes unchanged; staging may be deleted/recoverable.
- Use existing `freeze_bytes`, parsers and `ObservationRow` CSV. Do not create a generic provider framework. For a configured source with no audited parser, fail explicitly or mark validation evidence unavailable only if config labels it validation-only; never silently treat raw download as normalized data.
- Manifest records exact URL, retrieved_at UTC, raw hash, parser version, normalized relative path/hash and vintage status. Same bytes/config/retrieval timestamp fixture yield byte-stable derived artifacts.

## Offline backtest input contract

- `backtest_v1.json` identifies a frozen input bundle/manifest path, the exact five strategies and the Task 7 cost config. Paths resolve relative to the config file unless absolute.
- `backtest` reads only local frozen artifacts. Before calculations, verify every declared raw/derived SHA-256, schema/version, contiguous months, and required validation-status inputs. A corrupt/missing hash fails before creating output directory.
- A minimal deterministic bundle loader may use normalized JSON plus PIT CSVs, but it must construct the real Task 4–7 records and call `run_backtest`; do not implement a second portfolio calculator.
- Official World/LWLD NAV absence is emitted with the named failure state, never converted to PASS. Proxy validation failure does not erase backtest evidence but marks recommendation status unvalidated.

## Output package

Write sorted deterministic UTF-8/newline-stable JSON/CSV for at least:

- `signals.csv`
- `allocations.csv`
- `equity_curves.csv`
- `summary_metrics.json`
- `cost_decomposition.csv`
- `world_validation.json`
- `leveraged_validation.json`
- `run_manifest.json`

`run_manifest.json` includes config hash, input manifest/hash plus raw/derived hashes, code/package version identifier, retrieval/vintage IDs, cost/fee scenario, pre-tax/nominal labels, validation statuses and SHA-256 of every other output. Do not hash the manifest into itself. Sorted relative file paths are binding.

Two runs from the same frozen fixture/config into different directories must have identical relative file sets and bytes. No wall-clock timestamp may enter offline outputs; use the frozen retrieval/run-as-of timestamp from the input manifest.

## Reporting/documentation

- User-facing labels: public World proxy is never called MSCI World; monthly leveraged evidence is a proxy, not exact daily licensed history.
- README documents refresh versus offline backtest, official-source/frozen-vintage flow, output meanings, named validation failure states, pre-tax limitation (Rule E gross unit tests only), no live trading, and exact commands.
- No TODO/TBD/placeholders in code/tests/config/README.

## Required RED tests

1. CLI command parsing and existing evaluate compatibility.
2. Offline backtest succeeds with network patched to raise.
3. Refresh network access only through one adapter.
4. Multi-source refresh failure preserves prior published manifest/vintage.
5. Corrupt raw/derived hash fails before output calculation/directory creation.
6. Synthetic frozen bundle calls real `run_backtest` and writes every named artifact.
7. Two output dirs have identical file paths/bytes.
8. Output manifest hashes reconcile; no self-hash/wall-clock value.
9. Missing official NAV emits named validation failure, not skipped/PASS.
10. Malformed schema/path traversal/duplicate output path fails closed.

Run CLI focused tests, complete suite, `pip check`, compile check and placeholder/mislabel scan. Report all commands/results and the exact changed-file list. Do not perform a real network refresh in tests, do not add plugins/dependencies, and do not begin GDI.
