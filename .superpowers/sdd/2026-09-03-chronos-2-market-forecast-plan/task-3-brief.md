# Task 3 brief — Frozen five-covariate refresh transaction

Implement Task 3 from `docs/superpowers/plans/2026-09-03-chronos-2-market-forecast-plan.md` under TDD.

## Files

- Modify `perpetual_engine/chronos_data.py`.
- Modify `tests/test_chronos.py`.

## Required interfaces

- `refresh_chronos_data(config_path: Path, *, fetcher: Callable[[str], bytes] = fetch_url, retrieved_at: datetime | None = None) -> str`
- `load_covariate_table(config: ChronosConfig) -> tuple[MonthlyTable, str]`

## Binding requirements

- Fetch the five configured sources exactly once: ECB DFR, DGS10, Brent, BIS, and `CPIAUCNS`.
- Normalize exactly `month,ECB_DFR,US_TREASURY_10Y,BRENT_RETURN,BIS_USD_CREDIT_YOY,US_CPI_YOY`.
- Bind availability rules `effective_date`, `treasury_next_business_day`, `observation_plus_7_days`, `quarter_end_plus_4_months`, and `following_month_end`, plus the complete raw payload/provenance hash inventory, in the immutable vintage identity. A different retrieval timestamp creates a distinct provenance-bound vintage.
- Stage, validate, hash, and atomically publish under `data/chronos_v1/vintages/<vintage_id>/`; replace `current_manifest.json` last.
- A failed fifth source, parser, normalized value, hash, or pointer operation leaves the previous vintage and pointer usable and publishes no partial destination.
- Repeated identical bytes/config reuse only a byte-identical existing vintage.
- Offline loading validates resolved project/data/`.staging`/`vintages` containment, pointer, schema, config hash, vintage ID, the exact raw-file inventory, normalized hash, exact header/order, finite values, and immutable table arrays.
- Use only standard library and existing project helpers; no new dependency, no network in tests, no Git initialization.

## Verification and packaging

Capture RED before production changes. Run focused `ChronosRefreshTests`, then the complete suite once. Snapshot task files under `task-3-base` and `task-3-head`, produce `task-3-review.diff`, write `task-3-report.md`, and mark review pending in the ledger.
