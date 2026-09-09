from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import tempfile
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from perpetual_engine import __version__
from perpetual_engine import data_sources
from perpetual_engine.allocation import (
    WORLD_PROXY_ID,
    SignalSnapshot,
    compute_signals,
    defensive_monthly_return,
    splice_italy_cpi,
)
from perpetual_engine.backtest import STRATEGIES, BacktestConfig, BacktestResult, run_backtest
from perpetual_engine.data_sources import (
    SourceArtifact,
    _validated_windows_filename_key,
    freeze_bytes,
    parse_eurostat_hicp_json,
    parse_damodaran_annual,
    parse_damodaran_monthly,
    parse_fred_csv,
    parse_wdi_market_cap_json,
)
from perpetual_engine.io import canonical_json, remove_tree, sha256_file
from perpetual_engine.leveraged_proxy import FundingRate, LeveragedReturn, leveraged_monthly_return
from perpetual_engine.market_proxy import (
    PublicWorldInputs,
    build_public_world_monthly,
    normalize_fx_monthly,
    parse_french_archive,
)
from perpetual_engine.point_in_time import ObservationRow, load_observation_csv, validate_rows, write_observation_csv


OUTPUT_FILES = (
    "allocations.csv",
    "cost_decomposition.csv",
    "equity_curves.csv",
    "leveraged_validation.json",
    "run_manifest.json",
    "signals.csv",
    "summary_metrics.json",
    "world_validation.json",
)
_OUTPUT_KEYS = tuple(name.rsplit(".", 1)[0] for name in OUTPUT_FILES)
_HASH = re.compile(r"[0-9a-f]{64}")
_DERIVED_SCHEMAS = {
    "normalized_observations": "OBSERVATION_ROW_CSV_V1",
    "public_world_usd": "OBSERVATION_ROW_CSV_V1",
    "public_world_eur": "OBSERVATION_ROW_CSV_V1",
    "italy_cpi_proxy": "OBSERVATION_ROW_CSV_V1",
    "backtest_bundle": "BACKTEST_BUNDLE_V1",
}
_FUNDING_REGIMES = (
    ("FRED_DFF", "DFF", date(1979, 1, 1), date(1985, 12, 31)),
    ("FRED_USD1MTD156N", "USD1MTD156N", date(1986, 1, 1), date(2021, 8, 31)),
    ("FRED_SOFR", "SOFR", date(2021, 9, 1), date.max),
)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _read_object(path: Path, name: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return _object(json.load(handle), name)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} is malformed JSON") from error


def _utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result.astimezone(timezone.utc)


def _decimal(value: Any, name: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a decimal string") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _safe_relative(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{name} must be a safe relative POSIX path")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{name} path traversal is forbidden")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{name} path traversal is forbidden")
    for part in pure.parts:
        if part.rstrip(" .") != part:
            raise ValueError(f"{name} has a trailing dot or space")
        try:
            _validated_windows_filename_key(part)
        except ValueError as error:
            raise ValueError(f"{name} is not a Windows-safe path") from error
    return Path(*pure.parts)


def _windows_path_key(relative: Path) -> tuple[str, ...]:
    return tuple(_validated_windows_filename_key(part) for part in relative.parts)


def _identity_artifact_path(value: Any) -> str:
    relative = _safe_relative(value, "artifact path")
    parts = relative.parts
    if len(parts) >= 3 and parts[0].casefold() == "vintages":
        parts = parts[2:]
    return PurePosixPath(*parts).as_posix()


def compute_vintage_id(
    config_hash: str,
    sources: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> str:
    """Bind a vintage name to every immutable input that can affect a run."""
    if not isinstance(config_hash, str) or not _HASH.fullmatch(config_hash):
        raise ValueError("config_hash must be a lowercase SHA-256")
    source_identity = []
    for source in sources:
        url = source.get("url")
        raw_hash = source.get("raw_hash")
        parser_version = source.get("parser_version")
        if not isinstance(url, str) or not isinstance(parser_version, str):
            raise ValueError("vintage sources require url and parser_version")
        if raw_hash is not None and (not isinstance(raw_hash, str) or not _HASH.fullmatch(raw_hash)):
            raise ValueError("source raw_hash must be null or a lowercase SHA-256")
        source_identity.append({"url": url, "raw_hash": raw_hash, "parser_version": parser_version})
    derived_identity = []
    for artifact in artifacts:
        if artifact.get("kind") != "derived":
            continue
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("derived artifact SHA-256 must be lowercase hexadecimal")
        derived_identity.append(
            {
                "path": _identity_artifact_path(artifact.get("path")),
                "sha256": digest,
                "role": artifact.get("role"),
                "schema_version": artifact.get("schema_version"),
            }
        )
    identity = {
        "config_hash": config_hash,
        "sources": sorted(source_identity, key=lambda item: (item["url"], item["parser_version"])),
        "derived": sorted(derived_identity, key=lambda item: item["path"]),
    }
    return "VINTAGE-" + hashlib.sha256(canonical_json(identity)).hexdigest()


def _resolved_child(root: Path, relative: Path, name: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{name} path traversal is forbidden")
    return candidate


def _configured_path(config_path: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else config_path.parent / path


def _source_inventory(config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty array")
    result = tuple(_object(source, "source") for source in sources)
    ids = [source.get("source_id") for source in result]
    if any(not isinstance(source_id, str) or not source_id for source_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("source_id values must be non-empty and unique")
    normalized: list[str] = []
    for source in result:
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(f"{source['source_id']}: url must be an explicit HTTPS endpoint")
        if source.get("status") not in {"required", "validation-only"}:
            raise ValueError(f"{source['source_id']}: status must be required or validation-only")
        if not isinstance(source.get("parser_kind"), str) or not isinstance(source.get("parser_version"), str):
            raise ValueError(f"{source['source_id']}: parser kind/version are required")
        if source.get("normalized_path") is not None:
            relative = _safe_relative(source["normalized_path"], "normalized_path").as_posix()
            normalized.append(relative.casefold())
    if len(normalized) != len(set(normalized)):
        raise ValueError("duplicate normalized output path")
    return result


def _parse_source(
    source: Mapping[str, Any], artifact: SourceArtifact, normalized_root: Path
) -> tuple[str, str, tuple[Any, ...]]:
    parser_kind = source["parser_kind"]
    relative = _safe_relative(source.get("normalized_path"), "normalized_path")
    output = _resolved_child(normalized_root, relative, "normalized_path")
    if parser_kind == "observation_csv":
        rows = load_observation_csv(artifact.local_path)
    elif parser_kind == "damodaran_annual":
        rows = parse_damodaran_annual(artifact.local_path, artifact)
    elif parser_kind == "damodaran_monthly":
        rows = parse_damodaran_monthly(artifact.local_path, artifact)
    elif parser_kind.startswith("french_"):
        rows = parse_french_archive(artifact, parser_kind)
    elif parser_kind == "wdi_market_cap_json":
        aggregates = source.get("aggregates")
        if not isinstance(aggregates, list):
            raise ValueError("WDI aggregates must be an array")
        rows = parse_wdi_market_cap_json(
            artifact,
            indicator=source.get("indicator"),
            aggregates=tuple(aggregates),
        )
    elif parser_kind == "fred_csv":
        rows = parse_fred_csv(
            artifact,
            series_id=source.get("series_id"),
            unit=source.get("unit"),
            source_scale=source.get("source_scale"),
            frequency=source.get("frequency"),
            availability=source.get("availability"),
        )
    elif parser_kind == "eurostat_hicp_json":
        rows = parse_eurostat_hicp_json(artifact)
    else:
        raise ValueError(f"{source['source_id']}: no audited parser for {parser_kind}")
    write_observation_csv(rows, output)
    return relative.as_posix(), sha256_file(output), tuple(rows)


def _month_end(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _calendar_months(start: date, end: date) -> tuple[date, ...]:
    months: list[date] = []
    current = start.replace(day=1)
    while _month_end(current) <= end:
        months.append(_month_end(current))
        current = _next_month(current)
    return tuple(months)


def _signal_coverage_end(rows: Iterable[ObservationRow], series_id: str, start: date) -> date:
    months = {
        _month_end(row.observation_date)
        for row in rows
        if row.series_id == series_id
        and row.observation_date >= start
        and row.available_at
        <= datetime.combine(_month_end(row.observation_date), datetime.max.time(), timezone.utc)
    }
    if not months:
        raise ValueError(f"{series_id} has no usable monthly signal coverage")
    for month in _calendar_months(start, max(months)):
        if month not in months:
            raise ValueError(f"{series_id} coverage has an internal gap at {month.isoformat()}")
    return _month_end(_next_month(max(months).replace(day=1)))


def _funding_for_period(
    rows_by_source: Mapping[str, tuple[ObservationRow, ...]], required_through: date
) -> tuple[tuple[FundingRate, ...], date]:
    observations: list[ObservationRow] = []
    funding_end = required_through
    for source_id, series_id, start, end in _FUNDING_REGIMES:
        if start > required_through:
            break
        selected = tuple(
            row
            for row in rows_by_source[source_id]
            if start <= row.observation_date <= end
        )
        if not selected:
            raise ValueError(f"{series_id} funding segment is empty for the required period")
        observations.extend(selected)
        segment_end = min(end, required_through)
        latest = max(row.observation_date for row in selected)
        if (segment_end - latest).days > 7:
            funding_end = min(funding_end, _month_end(latest))
            break
    if len({row.observation_date for row in observations}) != len(observations):
        raise ValueError("filtered funding observations must have unique dates")
    return (
        tuple(FundingRate(row.observation_date, row.series_id, row.value) for row in observations),
        funding_end,
    )


def _write_derived_rows(
    stage: Path, relative: str, role: str, rows: Iterable[ObservationRow]
) -> dict[str, str]:
    path = stage / relative
    write_observation_csv(rows, path)
    return {
        "kind": "derived",
        "role": role,
        "path": relative,
        "sha256": sha256_file(path),
        "schema_version": "OBSERVATION_ROW_CSV_V1",
    }


def _build_refresh_derivatives(
    stage: Path,
    rows_by_source: Mapping[str, tuple[ObservationRow, ...]],
    artifacts_by_source: Mapping[str, SourceArtifact],
    retrieved_at: datetime,
    max_bundle_staleness_days: int,
) -> tuple[list[dict[str, str]], str, dict[str, str], date, date]:
    required_ids = {
        "DAMODARAN_ERP_ANNUAL",
        "DAMODARAN_ERP_MONTHLY",
        "FRENCH_INTERNATIONAL_MONTHLY",
        "FRENCH_US_MONTHLY",
        "FRENCH_DEVELOPED_MONTHLY",
        "WDI_MARKET_CAP",
        "FRED_SYNTHETIC_EUR_FX",
        "FRED_DEXUSEU",
        "FRED_DGS10",
        "FRED_DFII10",
        "FRED_DFF",
        "FRED_USD1MTD156N",
        "FRED_SOFR",
        "FRED_IR3TIB01ITM156N",
        "FRED_ITALY_CPI",
        "EUROSTAT_ITALY_HICP",
    }
    if not required_ids <= rows_by_source.keys() or not required_ids <= artifacts_by_source.keys():
        missing = sorted(required_ids - rows_by_source.keys())
        raise ValueError(f"required normalized sources are missing: {', '.join(missing)}")

    wdi_caps: dict[str, dict[int, Decimal]] = {"USA": {}, "WLD": {}}
    for row in rows_by_source["WDI_MARKET_CAP"]:
        aggregate = row.series_id.rsplit("_", 1)[-1]
        wdi_caps[aggregate][row.observation_date.year] = row.value
    world_usd = build_public_world_monthly(
        PublicWorldInputs(
            artifacts_by_source["FRENCH_INTERNATIONAL_MONTHLY"],
            artifacts_by_source["FRENCH_US_MONTHLY"],
            artifacts_by_source["FRENCH_DEVELOPED_MONTHLY"],
            wdi_caps,
            "https://api.worldbank.org/v2",
            artifacts_by_source["WDI_MARKET_CAP"].source_hash,
            artifacts_by_source["WDI_MARKET_CAP"].retrieved_at,
        )
    )
    fx_raw = tuple((*rows_by_source["FRED_SYNTHETIC_EUR_FX"], *rows_by_source["FRED_DEXUSEU"]))
    normalizable_fx = tuple(row for row in fx_raw if row.observation_date >= date(1977, 1, 1))
    fx_quotes = {quote.month: quote for quote in normalize_fx_monthly(normalizable_fx)}
    prior_raw = [row for row in fx_raw if row.observation_date < date(1977, 1, 1)]
    prior_eur_per_usd = Decimal("1") / prior_raw[-1].value if prior_raw else None
    world_eur: list[ObservationRow] = []
    prior_quote = prior_eur_per_usd
    world_fx_end = min(
        max(item.observation.observation_date for item in world_usd),
        max(fx_quotes),
    )
    for item in (item for item in world_usd if item.observation.observation_date <= world_fx_end):
        month = item.observation.observation_date
        try:
            quote = fx_quotes[month]
        except KeyError as error:
            raise ValueError(f"FX coverage has an internal gap at {month.isoformat()}") from error
        current_quote = quote.observation.value
        if prior_quote is None:
            raise ValueError("January 1977 FX conversion requires a prior frozen fixing")
        value = (Decimal("1") + item.observation.value) * (current_quote / prior_quote) - Decimal("1")
        parents = (item.observation.source_hash, quote.observation.source_hash)
        source_hash = hashlib.sha256(canonical_json(sorted(parents))).hexdigest()
        world_eur.append(
            ObservationRow(
                WORLD_PROXY_ID,
                month,
                month,
                max(item.observation.available_at, quote.observation.available_at),
                value,
                "ratio",
                "derived://public-developed-world/monthly-return-eur",
                max(item.observation.retrieved_at, quote.observation.retrieved_at),
                source_hash,
                quality_flags=(item.segment, "FX_APPLIED_ONCE"),
            )
        )
        prior_quote = current_quote
    world_eur_rows = validate_rows(world_eur)
    if not world_eur_rows or world_eur_rows[0].observation_date != date(1977, 1, 31):
        raise ValueError("EUR World warm-up must begin January 1977")

    cpi_rows = splice_italy_cpi(
        rows_by_source["FRED_ITALY_CPI"], rows_by_source["EUROSTAT_ITALY_HICP"]
    )
    derived = [
        _write_derived_rows(
            stage,
            "derived/public_world_usd.csv",
            "public_world_usd",
            (item.observation for item in world_usd),
        ),
        _write_derived_rows(stage, "derived/public_world_eur.csv", "public_world_eur", world_eur_rows),
        _write_derived_rows(stage, "derived/italy_cpi_proxy.csv", "italy_cpi_proxy", cpi_rows),
    ]

    erp_rows = tuple(
        replace(row, series_id="ERP")
        for row in (*rows_by_source["DAMODARAN_ERP_ANNUAL"], *rows_by_source["DAMODARAN_ERP_MONTHLY"])
    )
    treasury_rows = rows_by_source["FRED_DGS10"]
    tips_rows = rows_by_source["FRED_DFII10"]
    defensive_rate_rows = rows_by_source["FRED_IR3TIB01ITM156N"]
    usd_by_month = {item.observation.observation_date: item.observation.value for item in world_usd}
    eur_by_month = {row.observation_date: row.value for row in world_eur_rows}
    last_complete_month = _month_end(retrieved_at.date().replace(day=1) - timedelta(days=1))
    latest_defensive = max(row.observation_date for row in defensive_rate_rows)
    defensive_end = _month_end(_next_month(_next_month(latest_defensive.replace(day=1))))
    outcome_limit = min(last_complete_month, max(usd_by_month), max(eur_by_month), defensive_end)
    funding_rows, funding_end = _funding_for_period(rows_by_source, outcome_limit)
    treasury_end = _signal_coverage_end(treasury_rows, "DGS10", date(1978, 12, 1))
    expected_through = min(outcome_limit, funding_end, treasury_end)
    if expected_through >= date(2003, 2, 28):
        expected_through = min(
            expected_through,
            _signal_coverage_end(tips_rows, "DFII10", date(2003, 1, 1)),
        )
    if expected_through < date(1979, 1, 31):
        raise ValueError("required outcomes do not cover the January 1979 backtest start")
    if (retrieved_at.date() - expected_through).days > max_bundle_staleness_days:
        raise ValueError("bundle end is stale relative to retrieved_at")
    signals: dict[date, SignalSnapshot] = {}
    defensive_returns: dict[date, Decimal] = {}
    leveraged_returns: dict[date, LeveragedReturn] = {}
    for month in _calendar_months(date(1979, 1, 31), expected_through):
        decision_day = month.replace(day=1) - timedelta(days=1)
        decision_at = datetime(
            decision_day.year, decision_day.month, decision_day.day, 23, 59, 59, tzinfo=timezone.utc
        )
        snapshot = compute_signals(
            decision_at=decision_at,
            erp_rows=erp_rows,
            treasury_rows=treasury_rows,
            tips_rows=tips_rows,
            market_rows=world_eur_rows,
        )
        if snapshot.treasury_10y is None:
            raise ValueError(f"DGS10 signal is missing for allocation month {month.isoformat()}")
        if month >= date(2003, 2, 28) and snapshot.tips is None:
            raise ValueError(f"DFII10 signal is missing for allocation month {month.isoformat()}")
        defensive = defensive_monthly_return(
            defensive_rate_rows, month.replace(day=1), decision_at, max_staleness_days=92
        )
        current_fx = fx_quotes[month].observation.value
        prior_month = month.replace(day=1) - timedelta(days=1)
        previous_fx = fx_quotes[_month_end(prior_month)].observation.value
        month_start = month.replace(day=1)
        month_funding = tuple(
            row
            for row in funding_rows
            if month_start - timedelta(days=7) <= row.observation_date <= month
        )
        leveraged = leveraged_monthly_return(
            usd_by_month[month],
            month_funding,
            month_start,
            current_fx / previous_fx,
            investable=True,
        )
        signals[month] = snapshot
        defensive_returns[month] = defensive
        leveraged_returns[month] = leveraged
    if not signals or tuple(signals)[0] != date(1979, 1, 31):
        raise ValueError("frozen official inputs do not produce a January 1979 backtest start")
    actual_end = tuple(signals)[-1]
    if actual_end != expected_through:
        raise ValueError("bundle actual_end does not equal expected_through")

    world_status = "FAIL_MISSING_OFFICIAL_NAV"
    leveraged_status = "FAIL_MISSING_OFFICIAL_LWLD_NAV"
    bundle = {
        "schema_version": "BACKTEST_BUNDLE_V1",
        "expected_through": expected_through.isoformat(),
        "actual_end": actual_end.isoformat(),
        "signals": [{"month": month.isoformat(), **_primitive(snapshot)} for month, snapshot in signals.items()],
        "world_returns": [{"month": month.isoformat(), "value": format(eur_by_month[month], "f")} for month in signals],
        "defensive_returns": [
            {"month": month.isoformat(), "value": format(defensive_returns[month], "f")} for month in signals
        ],
        "leveraged_returns": [
            {"month": month.isoformat(), **_primitive(leveraged_returns[month])} for month in signals
        ],
        "world_validation": {"status": world_status, "n": 0},
        "leveraged_validation": {
            "licensed_daily": {"status": "FAIL_INSUFFICIENT_DAILY_SAMPLE", "n": 0},
            "official_summary": {"status": "FAIL_OFFICIAL_SUMMARY_VALIDATION", "n": 0},
            "lwld": {"status": leveraged_status, "n": 0},
        },
    }
    bundle_relative = "derived/backtest_bundle.json"
    bundle_path = stage / bundle_relative
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_bytes(canonical_json(bundle))
    derived.append(
        {
            "kind": "derived",
            "role": "backtest_bundle",
            "path": bundle_relative,
            "sha256": sha256_file(bundle_path),
            "schema_version": "BACKTEST_BUNDLE_V1",
        }
    )
    return (
        derived,
        bundle_relative,
        {"world": world_status, "leveraged": leveraged_status},
        expected_through,
        actual_end,
    )


def refresh_data(config_path: Path, *, retrieved_at: datetime | None = None) -> int:
    """Refresh configured official sources and publish one manifest transaction."""
    config_path = Path(config_path)
    config = _read_object(config_path, "data-source config")
    if config.get("schema_version") != "DATA_SOURCES_V1":
        raise ValueError("data-source config schema_version must be DATA_SOURCES_V1")
    sources = _source_inventory(config)
    data_root = _configured_path(config_path, config.get("output_data_root"), "output_data_root")
    retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if retrieved_at is not None and (retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None):
        raise ValueError("retrieved_at must be timezone-aware")
    staging_root = data_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="refresh-", dir=staging_root))
    published_created: Path | None = None
    temporary_pointer: Path | None = None
    downloads: list[tuple[dict[str, Any], SourceArtifact | None, str | None]] = []
    try:
        for source in sources:
            try:
                content = data_sources.fetch_url(source["url"])
                artifact = freeze_bytes(
                    content,
                    source["url"],
                    retrieved,
                    stage / "raw",
                    source["parser_version"],
                )
                downloads.append((source, artifact, None))
            except (OSError, ValueError) as error:
                if source["status"] == "required":
                    raise
                downloads.append((source, None, type(error).__name__))

        source_records: list[dict[str, Any]] = []
        raw_artifacts: list[dict[str, str]] = []
        derived_artifacts: list[dict[str, str]] = []
        rows_by_source: dict[str, tuple[ObservationRow, ...]] = {}
        artifacts_by_source: dict[str, SourceArtifact] = {}
        for source, artifact, failure in downloads:
            record: dict[str, Any] = {
                "source_id": source["source_id"],
                "url": source["url"],
                "retrieved_at": retrieved.isoformat(),
                "parser_kind": source["parser_kind"],
                "parser_version": source["parser_version"],
                "required_status": source["status"],
            }
            if artifact is None:
                record.update(
                    raw_hash=None,
                    normalized_path=None,
                    normalized_hash=None,
                    vintage_status="VALIDATION_EVIDENCE_UNAVAILABLE",
                    failure=failure,
                )
                source_records.append(record)
                continue
            raw_relative = artifact.local_path.relative_to(stage).as_posix()
            raw_artifacts.append({"kind": "raw", "path": raw_relative, "sha256": artifact.source_hash})
            record["raw_hash"] = artifact.source_hash
            artifacts_by_source[source["source_id"]] = artifact
            if source["status"] == "validation-only" and source["parser_kind"] == "none":
                record.update(
                    normalized_path=None,
                    normalized_hash=None,
                    vintage_status="VALIDATION_ONLY_RAW",
                )
            else:
                normalized_relative, normalized_hash, normalized_rows = _parse_source(
                    source, artifact, stage / "normalized"
                )
                manifest_relative = (Path("normalized") / normalized_relative).as_posix()
                derived_artifacts.append(
                    {
                        "kind": "derived",
                        "role": "normalized_observations",
                        "path": manifest_relative,
                        "sha256": normalized_hash,
                        "schema_version": "OBSERVATION_ROW_CSV_V1",
                    }
                )
                record.update(
                    normalized_path=manifest_relative,
                    normalized_hash=normalized_hash,
                    vintage_status="CURRENT_VINTAGE_RESEARCH",
                )
                rows_by_source[source["source_id"]] = normalized_rows
            source_records.append(record)

        build_bundle = config.get("build_backtest_bundle", True)
        if not isinstance(build_bundle, bool):
            raise ValueError("build_backtest_bundle must be boolean")
        if build_bundle:
            max_bundle_staleness_days = config.get("max_bundle_staleness_days")
            if (
                not isinstance(max_bundle_staleness_days, int)
                or isinstance(max_bundle_staleness_days, bool)
                or max_bundle_staleness_days < 0
            ):
                raise ValueError("max_bundle_staleness_days must be a non-negative integer")
            bundle_artifacts, bundle_relative, validation_statuses, expected_through, actual_end = _build_refresh_derivatives(
                stage,
                rows_by_source,
                artifacts_by_source,
                retrieved,
                max_bundle_staleness_days,
            )
            derived_artifacts.extend(bundle_artifacts)
        else:
            bundle_relative = ""
            validation_statuses = {}

        config_hash = sha256_file(config_path)
        vintage_id = compute_vintage_id(config_hash, source_records, (*raw_artifacts, *derived_artifacts))
        prefix = f"vintages/{vintage_id}/"
        artifacts = sorted(
            ({**entry, "path": prefix + entry["path"]} for entry in (*raw_artifacts, *derived_artifacts)),
            key=lambda entry: entry["path"],
        )
        for record in source_records:
            if record.get("normalized_path") is not None:
                record["normalized_path"] = prefix + record["normalized_path"]
        manifest = {
            "schema_version": "BACKTEST_INPUT_MANIFEST_V1" if build_bundle else "FROZEN_DATA_MANIFEST_V1",
            "config_hash": config_hash,
            "retrieved_at": retrieved.isoformat(),
            "run_as_of": retrieved.isoformat(),
            "vintage_id": vintage_id,
            "artifacts": artifacts,
            "sources": sorted(source_records, key=lambda record: record["source_id"]),
        }
        if build_bundle:
            manifest["bundle_path"] = prefix + bundle_relative
            manifest["validation_statuses"] = validation_statuses
            manifest["expected_through"] = expected_through.isoformat()
            manifest["actual_end"] = actual_end.isoformat()
        manifest_bytes = canonical_json(manifest)
        (stage / "manifest.json").write_bytes(manifest_bytes)
        vintages = data_root / "vintages"
        vintages.mkdir(parents=True, exist_ok=True)
        published = vintages / vintage_id
        if published.exists():
            if (published / "manifest.json").read_bytes() != manifest_bytes:
                raise ValueError("vintage identity collision")
            remove_tree(stage)
        else:
            stage.replace(published)
            published_created = published
        pointer = data_root / "current_manifest.json"
        temporary_pointer = data_root / f".current_manifest.{vintage_id}.tmp"
        temporary_pointer.write_bytes(manifest_bytes)
        temporary_pointer.replace(pointer)
        return 0
    except Exception:
        if temporary_pointer is not None and temporary_pointer.exists():
            temporary_pointer.unlink()
        if published_created is not None and published_created.exists():
            remove_tree(published_created)
        if stage.exists():
            remove_tree(stage)
        raise


def _artifact_entries(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if "artifacts" in manifest:
        entries = manifest["artifacts"]
        if not isinstance(entries, list):
            raise ValueError("artifacts must be an array")
        return tuple(_object(entry, "artifact") for entry in entries)
    combined: list[dict[str, Any]] = []
    for key, kind in (("raw_artifacts", "raw"), ("derived_artifacts", "derived")):
        entries = manifest.get(key)
        if not isinstance(entries, list):
            raise ValueError(f"{key} must be an array")
        combined.extend(({**_object(entry, "artifact"), "kind": kind} for entry in entries))
    return tuple(combined)


def _preflight_manifest(path: Path) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], Path]:
    manifest = _read_object(path, "input manifest")
    if manifest.get("schema_version") != "BACKTEST_INPUT_MANIFEST_V1":
        raise ValueError("input manifest schema_version must be BACKTEST_INPUT_MANIFEST_V1")
    entries = _artifact_entries(manifest)
    if not entries:
        raise ValueError("input manifest must declare artifacts")
    relative_paths = [_safe_relative(entry.get("path"), "artifact path") for entry in entries]
    keys = [_windows_path_key(relative) for relative in relative_paths]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate artifact path")
    kinds = {entry.get("kind") for entry in entries}
    if not {"raw", "derived"} <= kinds or kinds - {"raw", "derived"}:
        raise ValueError("input manifest requires raw and derived artifacts")
    for entry in entries:
        if entry.get("kind") != "derived":
            continue
        role = entry.get("role")
        expected_schema = _DERIVED_SCHEMAS.get(role)
        if expected_schema is None or entry.get("schema_version") != expected_schema:
            raise ValueError("derived artifact schema_version is missing or not allowed for its role")
    for entry, relative in zip(entries, relative_paths):
        expected = entry.get("sha256")
        if not isinstance(expected, str) or not _HASH.fullmatch(expected):
            raise ValueError("artifact SHA-256 must be lowercase hexadecimal")
        artifact_path = _resolved_child(path.parent, relative, "artifact path")
        if not artifact_path.is_file() or sha256_file(artifact_path) != expected:
            raise ValueError(f"artifact SHA-256 mismatch: {relative.as_posix()}")
    statuses = manifest.get("validation_statuses")
    if not isinstance(statuses, dict) or any(not isinstance(statuses.get(key), str) for key in ("world", "leveraged")):
        raise ValueError("required validation statuses are missing")
    _utc(manifest.get("retrieved_at"), "retrieved_at")
    _utc(manifest.get("run_as_of"), "run_as_of")
    if not isinstance(manifest.get("vintage_id"), str) or not manifest["vintage_id"]:
        raise ValueError("vintage_id is required")
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources must be an array")
    if compute_vintage_id(manifest.get("config_hash"), sources, entries) != manifest["vintage_id"]:
        raise ValueError("vintage_id does not reconcile with frozen inputs")
    bundle_relative = _safe_relative(manifest.get("bundle_path"), "bundle_path")
    by_path = {relative.as_posix(): entry for relative, entry in zip(relative_paths, entries)}
    bundle_entry = by_path.get(bundle_relative.as_posix())
    if bundle_entry is None or bundle_entry.get("kind") != "derived":
        raise ValueError("bundle_path must name a declared derived artifact")
    if bundle_entry.get("role") != "backtest_bundle" or bundle_entry.get("schema_version") != "BACKTEST_BUNDLE_V1":
        raise ValueError("bundle artifact schema_version must be BACKTEST_BUNDLE_V1")
    return manifest, entries, _resolved_child(path.parent, bundle_relative, "bundle_path")


def _month(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        result = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    return result


def _unique_month_rows(value: Any, name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    rows = tuple(_object(row, name) for row in value)
    months = [_month(row.get("month"), f"{name}.month") for row in rows]
    if len(months) != len(set(months)):
        raise ValueError(f"{name} has duplicate months")
    return rows


def _signals(value: Any) -> dict[date, SignalSnapshot]:
    result: dict[date, SignalSnapshot] = {}
    for row in _unique_month_rows(value, "signals"):
        month = _month(row["month"], "signals.month")
        result[month] = SignalSnapshot(
            decision_at=_utc(row.get("decision_at"), "signals.decision_at"),
            erp=_decimal(row.get("erp"), "signals.erp"),  # type: ignore[arg-type]
            erp_percentile=_decimal(row.get("erp_percentile"), "signals.erp_percentile", optional=True),
            erp_history_count=row.get("erp_history_count"),
            treasury_10y=_decimal(row.get("treasury_10y"), "signals.treasury_10y", optional=True),
            tips=_decimal(row.get("tips"), "signals.tips", optional=True),
            tips_percentile=_decimal(row.get("tips_percentile"), "signals.tips_percentile", optional=True),
            tips_history_count=row.get("tips_history_count"),
            drawdown=_decimal(row.get("drawdown"), "signals.drawdown", optional=True),
            momentum_1m=_decimal(row.get("momentum_1m"), "signals.momentum_1m", optional=True),
            momentum_3m=_decimal(row.get("momentum_3m"), "signals.momentum_3m", optional=True),
            volatility_12m=_decimal(row.get("volatility_12m"), "signals.volatility_12m", optional=True),
            market_history_count=row.get("market_history_count"),
        )
    return result


def _decimal_series(value: Any, name: str) -> dict[date, Decimal]:
    return {
        _month(row["month"], f"{name}.month"): _decimal(row.get("value"), f"{name}.value")  # type: ignore[misc]
        for row in _unique_month_rows(value, name)
    }


def _leveraged(value: Any) -> dict[date, LeveragedReturn]:
    result: dict[date, LeveragedReturn] = {}
    for row in _unique_month_rows(value, "leveraged_returns"):
        flags = row.get("flags")
        if not isinstance(flags, list) or any(not isinstance(flag, str) or not flag for flag in flags):
            raise ValueError("leveraged flags must be an array of non-empty strings")
        if not isinstance(row.get("wiped_out"), bool):
            raise ValueError("leveraged wiped_out must be boolean")
        result[_month(row["month"], "leveraged_returns.month")] = LeveragedReturn(
            underlying=_decimal(row.get("underlying"), "leveraged underlying"),  # type: ignore[arg-type]
            funding=_decimal(row.get("funding"), "leveraged funding"),  # type: ignore[arg-type]
            residual_drag=_decimal(row.get("residual_drag"), "leveraged residual_drag"),  # type: ignore[arg-type]
            fx=_decimal(row.get("fx"), "leveraged fx"),  # type: ignore[arg-type]
            etf_fee=_decimal(row.get("etf_fee"), "leveraged etf_fee"),  # type: ignore[arg-type]
            total_return=_decimal(row.get("total_return"), "leveraged total_return"),  # type: ignore[arg-type]
            return_usd=_decimal(row.get("return_usd"), "leveraged return_usd"),  # type: ignore[arg-type]
            flags=tuple(flags),
            wiped_out=row["wiped_out"],
        )
    return result


def _validate_bundle_calendar(
    signals: Mapping[date, SignalSnapshot],
    world_returns: Mapping[date, Decimal],
    defensive_returns: Mapping[date, Decimal],
    leveraged_returns: Mapping[date, LeveragedReturn],
) -> None:
    months = tuple(sorted(signals))
    if not months or months[0] != date(1979, 1, 31):
        raise ValueError("bundle calendar must start in January 1979")
    if any(set(series) != set(months) for series in (world_returns, defensive_returns, leveraged_returns)):
        raise ValueError("bundle series must contain the same months")
    for previous, current in zip(months, months[1:]):
        following_start = date(previous.year + (previous.month == 12), previous.month % 12 + 1, 1)
        following_end = date(
            following_start.year + (following_start.month == 12), following_start.month % 12 + 1, 1
        ) - timedelta(days=1)
        if current != following_end:
            raise ValueError("bundle calendar must be contiguous")


def _validate_bundle_end_metadata(
    manifest: Mapping[str, Any], bundle: Mapping[str, Any], actual_end: date
) -> None:
    values = {
        (owner, field): _month(payload.get(field), f"{owner}.{field}")
        for owner, payload in (("manifest", manifest), ("bundle", bundle))
        for field in ("expected_through", "actual_end")
    }
    if any(value != actual_end for value in values.values()):
        raise ValueError("manifest and bundle expected_through/actual_end must equal the calendar end")


def _json_value(value: Any, name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} contains a non-finite number")
        return value
    if isinstance(value, list):
        return [_json_value(item, name) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{name} keys must be strings")
        return {key: _json_value(item, name) for key, item in value.items()}
    raise ValueError(f"{name} contains an unsupported value")


def _validation_payloads(bundle: Mapping[str, Any], statuses: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    world_value = bundle.get("world_validation")
    world = (
        {"status": "FAIL_MISSING_OFFICIAL_NAV", "n": 0}
        if world_value is None
        else _object(_json_value(world_value, "world_validation"), "world_validation")
    )
    leveraged_value = bundle.get("leveraged_validation")
    leveraged = (
        {
            "licensed_daily": {"status": "FAIL_INSUFFICIENT_DAILY_SAMPLE", "n": 0},
            "official_summary": {"status": "FAIL_OFFICIAL_SUMMARY_VALIDATION", "n": 0},
            "lwld": {"status": "FAIL_MISSING_OFFICIAL_LWLD_NAV", "n": 0},
        }
        if leveraged_value is None
        else _object(_json_value(leveraged_value, "leveraged_validation"), "leveraged_validation")
    )
    try:
        leveraged_status = leveraged["lwld"]["status"]
    except (KeyError, TypeError) as error:
        raise ValueError("leveraged validation requires lwld.status") from error
    if world.get("status") != statuses["world"] or leveraged_status != statuses["leveraged"]:
        raise ValueError("validation statuses do not reconcile with the input manifest")
    return world, leveraged


def _recommendation_status(
    world_validation: Mapping[str, Any], leveraged_validation: Mapping[str, Any]
) -> str:
    try:
        world_pass = world_validation.get("status") == "PASS"
        lwld_pass = leveraged_validation["lwld"].get("status") == "PASS_LWLD_VALIDATION"
        benchmark_pass = (
            leveraged_validation["licensed_daily"].get("status") == "PASS_FULL_DAILY"
            or leveraged_validation["official_summary"].get("status")
            == "PASS_PARTIAL_OFFICIAL_SUMMARY"
        )
    except (AttributeError, KeyError, TypeError):
        return "UNVALIDATED"
    return "VALIDATED" if world_pass and lwld_pass and benchmark_pass else "UNVALIDATED"


def _primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _csv_bytes(rows: Iterable[Mapping[str, Any]], columns: tuple[str, ...]) -> bytes:
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _primitive(row.get(column)) for column in columns})
    return output.getvalue().encode("utf-8")


def _output_names(config: Mapping[str, Any]) -> dict[str, str]:
    defaults = dict(zip(_OUTPUT_KEYS, OUTPUT_FILES))
    configured = config.get("output_files", defaults)
    if not isinstance(configured, dict) or set(configured) != set(defaults):
        raise ValueError("output_files must define every required output")
    names = {key: _safe_relative(configured[key], "output path").as_posix() for key in defaults}
    path_keys = [_windows_path_key(Path(*PurePosixPath(name).parts)) for name in names.values()]
    if len(set(path_keys)) != len(names):
        raise ValueError("duplicate output path")
    for possible_parent in path_keys:
        for possible_child in path_keys:
            if len(possible_parent) < len(possible_child) and possible_child[: len(possible_parent)] == possible_parent:
                raise ValueError("output path has a file/directory ancestor collision")
    return names


def _backtest_config(config: Mapping[str, Any]) -> BacktestConfig:
    if config.get("schema_version") != "BACKTEST_CONFIG_V1":
        raise ValueError("backtest config schema_version must be BACKTEST_CONFIG_V1")
    if tuple(config.get("strategies", ())) != STRATEGIES:
        raise ValueError("backtest config must contain the exact five strategies")
    if config.get("world_annual_fee") != "0.002" or config.get("leveraged_annual_fee") != "0.006":
        raise ValueError("backtest fee configuration must match Task 7")
    return BacktestConfig(
        initial_capital_eur=_decimal(config.get("initial_capital_eur"), "initial_capital_eur"),  # type: ignore[arg-type]
        commission_per_order_eur=_decimal(
            config.get("commission_per_executed_sleeve_order_eur"), "commission_per_executed_sleeve_order_eur"
        ),  # type: ignore[arg-type]
        spread_slippage_bps=_decimal(config.get("spread_slippage_bps"), "spread_slippage_bps"),  # type: ignore[arg-type]
        config_id=str(config.get("version")),
    )


def _write_outputs(
    output_dir: Path,
    names: Mapping[str, str],
    result: BacktestResult,
    signals: Mapping[date, SignalSnapshot],
    world_validation: Mapping[str, Any],
    leveraged_validation: Mapping[str, Any],
    run_metadata: Mapping[str, Any],
) -> None:
    if output_dir.exists():
        raise ValueError("output directory already exists")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        signal_rows = ({"month": month, **_primitive(snapshot)} for month, snapshot in sorted(signals.items()))
        signal_columns = ("month", *tuple(field.name for field in fields(SignalSnapshot)))
        (stage / names["signals"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["signals"]).write_bytes(_csv_bytes(signal_rows, signal_columns))

        allocation_columns = (
            "strategy", "month", "decision_at", "state", "trigger", "accepted_final_beta", "actual_beta",
            "pretrade_core_weight", "pretrade_overlay_weight", "pretrade_defensive_weight", "target_core_weight",
            "target_overlay_weight", "target_defensive_weight", "core_trade", "overlay_trade", "defensive_trade",
            "allocation_flags", "leveraged_flags",
        )
        allocation_rows = []
        for row in result.rows:
            item = _primitive(row)
            item["allocation_flags"] = "|".join(row.allocation_flags)
            item["leveraged_flags"] = "|".join(row.leveraged_flags)
            allocation_rows.append(item)
        (stage / names["allocations"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["allocations"]).write_bytes(_csv_bytes(allocation_rows, allocation_columns))

        curve_columns = (
            "strategy", "month", "pretrade_nav", "post_cost_nav", "ending_core", "ending_overlay",
            "ending_defensive", "ending_nav", "portfolio_return",
        )
        (stage / names["equity_curves"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["equity_curves"]).write_bytes(
            _csv_bytes((_primitive(row) for row in result.rows), curve_columns)
        )
        (stage / names["summary_metrics"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["summary_metrics"]).write_bytes(canonical_json({"summaries": _primitive(result.summaries)}))

        cost_columns = (
            "strategy", "turnover", "commissions_eur", "spread_eur", "funding_eur", "world_fee_eur",
            "leveraged_fee_eur", "residual_drag_eur",
        )
        costs = []
        for summary in result.summaries:
            costs.append({"strategy": summary.strategy, "turnover": summary.turnover, **_primitive(summary.costs)})
        (stage / names["cost_decomposition"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["cost_decomposition"]).write_bytes(_csv_bytes(costs, cost_columns))
        (stage / names["world_validation"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["world_validation"]).write_bytes(canonical_json(world_validation))
        (stage / names["leveraged_validation"]).parent.mkdir(parents=True, exist_ok=True)
        (stage / names["leveraged_validation"]).write_bytes(canonical_json(leveraged_validation))

        manifest_name = names["run_manifest"]
        output_hashes = {
            relative.as_posix(): sha256_file(path)
            for path in sorted(stage.rglob("*"))
            if path.is_file() and (relative := path.relative_to(stage)).as_posix() != manifest_name
        }
        manifest = {**run_metadata, "output_hashes": dict(sorted(output_hashes.items()))}
        (stage / manifest_name).parent.mkdir(parents=True, exist_ok=True)
        (stage / manifest_name).write_bytes(canonical_json(manifest))
        stage.replace(output_dir)
    except Exception:
        if stage.exists():
            remove_tree(stage)
        raise


def run_backtest_report(config_path: Path, output_dir: Path) -> int:
    """Verify a frozen bundle, run Task 7, and atomically publish stable reports."""
    config_path = Path(config_path)
    config = _read_object(config_path, "backtest config")
    engine_config = _backtest_config(config)
    names = _output_names(config)
    manifest_path = _configured_path(config_path, config.get("input_manifest"), "input_manifest")
    manifest, entries, bundle_path = _preflight_manifest(manifest_path)
    bundle = _read_object(bundle_path, "backtest bundle")
    if bundle.get("schema_version") != "BACKTEST_BUNDLE_V1":
        raise ValueError("backtest bundle schema_version must be BACKTEST_BUNDLE_V1")
    signals = _signals(bundle.get("signals"))
    world_returns = _decimal_series(bundle.get("world_returns"), "world_returns")
    defensive_returns = _decimal_series(bundle.get("defensive_returns"), "defensive_returns")
    leveraged_returns = _leveraged(bundle.get("leveraged_returns"))
    _validate_bundle_calendar(signals, world_returns, defensive_returns, leveraged_returns)
    _validate_bundle_end_metadata(manifest, bundle, max(signals))
    world_validation, leveraged_validation = _validation_payloads(bundle, manifest["validation_statuses"])
    result = run_backtest(
        signals=signals,
        world_returns=world_returns,
        defensive_returns=defensive_returns,
        leveraged_returns=leveraged_returns,
        config=engine_config,
    )
    artifacts = {
        entry["path"]: {
            key: entry[key]
            for key in ("kind", "role", "schema_version", "sha256")
            if key in entry
        }
        for entry in sorted(entries, key=lambda entry: entry["path"])
    }
    metadata = {
        "schema_version": "BACKTEST_RUN_MANIFEST_V1",
        "code_version": f"perpetual_engine-{__version__}",
        "config_hash": sha256_file(config_path),
        "input_manifest": config["input_manifest"],
        "input_manifest_hash": sha256_file(manifest_path),
        "input_artifacts": artifacts,
        "retrieval_id": manifest["retrieved_at"],
        "run_as_of": manifest["run_as_of"],
        "vintage_id": manifest["vintage_id"],
        "cost_fee_scenario": _primitive(result.metadata),
        "capital_unit": "NOMINAL_MODEL_EUR",
        "tax_treatment": "PRE_TAX",
        "validation_statuses": manifest["validation_statuses"],
        "recommendation_status": _recommendation_status(world_validation, leveraged_validation),
    }
    _write_outputs(
        Path(output_dir), names, result, signals, world_validation, leveraged_validation, metadata
    )
    return 0
