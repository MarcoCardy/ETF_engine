from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yfinance as yf

from perpetual_engine.data_sources import fetch_url
from perpetual_engine.dro import convert_usd_monthly_levels_to_eur, load_dro_config, normalize_month_end_observations, validate_vintage_manifest
from perpetual_engine.io import canonical_json


ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "dro_v1.json"
DATA = ROOT / "data" / "dro_v1"


def _canonical_rows(rows: list[tuple[date, Decimal]]) -> bytes:
    return ("date,level\n" + "".join(f"{observed.isoformat()},{level}\n" for observed, level in rows)).encode("utf-8")


def _fred_rows(content: bytes, series_id: str) -> list[tuple[date, Decimal]]:
    reader = csv.DictReader(content.decode("utf-8-sig").splitlines())
    if reader.fieldnames != ["observation_date", series_id]:
        raise ValueError(f"FRED {series_id} response columns changed")
    return [(date.fromisoformat(row["observation_date"]), Decimal(row[series_id])) for row in reader if row[series_id] not in {"", "."}]


def _ecb_rows(content: bytes) -> list[tuple[date, Decimal]]:
    reader = csv.DictReader(content.decode("utf-8-sig").splitlines())
    if "TIME_PERIOD" not in (reader.fieldnames or ()) or "OBS_VALUE" not in (reader.fieldnames or ()):
        raise ValueError("ECB response columns changed")
    return [(date.fromisoformat(row["TIME_PERIOD"]), Decimal(row["OBS_VALUE"])) for row in reader if row["OBS_VALUE"] not in {"", "."}]


def _lbma_rows(content: bytes) -> list[tuple[date, Decimal]]:
    try:
        payload = json.loads(content.decode("utf-8"))
        rows = [(date.fromisoformat(item["d"]), Decimal(str(item["v"][0]))) for item in payload if item["v"][0] is not None]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("LBMA Gold PM response changed") from error
    if not rows:
        raise ValueError("LBMA Gold PM has no usable prices")
    return rows


def _period_targets(start: date, full_end: date, partial_as_of: date) -> tuple[date, ...]:
    targets = []
    current = date(start.year, start.month, 1)
    while current <= full_end:
        target = date(current.year + (current.month == 12), current.month % 12 + 1, 1) - timedelta(days=1)
        if target >= start:
            targets.append(target)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return (*targets, partial_as_of)


def monthly_euro_cash_returns(
    deposit_facility: tuple[tuple[date, Decimal], ...], estr: tuple[tuple[date, Decimal], ...], *, start: date, full_end: date, partial_as_of: date
) -> dict[date, tuple[str, date, Decimal]]:
    """Freeze monthly EUR overnight returns; it is an input series, not strategy performance."""
    if not deposit_facility or not estr:
        raise ValueError("both ECB cash source histories are required")
    result: dict[date, tuple[str, date, Decimal]] = {}
    transition = date(2019, 10, 1)
    for target in _period_targets(start, full_end, partial_as_of):
        source_id, source_rows = ("ECB_ESTR", estr) if target >= transition else ("ECB_DEPOSIT_FACILITY", deposit_facility)
        factor, day, latest = Decimal("1"), date(target.year, target.month, 1), None
        while day <= target:
            matches = [row for row in source_rows if row[0] <= day]
            if not matches:
                raise ValueError(f"{source_id} has no rate available for {day.isoformat()}")
            latest = matches[-1]
            factor *= Decimal("1") + latest[1] / Decimal("100") / Decimal("360")
            day += timedelta(days=1)
        result[target] = (source_id, latest[0], factor - Decimal("1"))
    return result


def _yfinance_rows(ticker: str, end: date) -> list[tuple[date, Decimal]]:
    history = yf.Ticker(ticker).history(start="2018-12-01", end=(end + timedelta(days=1)).isoformat(), auto_adjust=False, actions=False)
    if history.empty or "Adj Close" not in history:
        raise ValueError(f"yfinance {ticker} has no adjusted-close history")
    return [(stamp.date(), Decimal(str(value))) for stamp, value in history["Adj Close"].items() if value == value and value > 0]


def _write_raw(raw_dir: Path, source_id: str, content: bytes) -> dict[str, object]:
    path = raw_dir / f"{source_id}.csv"
    path.write_bytes(content)
    return {"path": path.relative_to(DATA).as_posix(), "source_hash": hashlib.sha256(content).hexdigest(), "byte_count": len(content)}


def _normalized_manifest_records() -> list[dict[str, object]]:
    records = []
    for filename in ("monthly_total_return_eur.csv", "monthly_benchmark_total_return_eur.csv", "monthly_cash_return_eur.csv"):
        path = DATA / filename
        content = path.read_bytes()
        with path.open("r", encoding="utf-8", newline="") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        records.append({"path": filename, "byte_count": len(content), "sha256": hashlib.sha256(content).hexdigest(), "row_count": row_count})
    return records


def refresh_dro_v1() -> Path:
    """Fetch and freeze declared input levels. It does not calculate any strategy return."""
    config_raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    config = load_dro_config(CONFIG)
    raw_dir = DATA / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, object]] = []
    observations: dict[str, dict[date, tuple[date, Decimal]]] = {}
    retrieval_currencies: dict[str, str] = {}

    for source in (item for item in config_raw["sources"] if item["track"] in {"CONDITIONAL_INDEX_PROXY", "BENCHMARK_ONLY"}):
        source_id = source["source_id"]
        if source["vendor"] == "FRED":
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={source['ticker']}"
            content = fetch_url(url)
            rows = _fred_rows(content, source["ticker"])
            raw_format = "FRED_CSV"
        elif source["vendor"] == "LBMA":
            url = source["source_url"]
            content = fetch_url(url)
            rows = _lbma_rows(content)
            raw_format = "LBMA_GOLD_PM_JSON"
        else:
            fallback = source["programmatic_fallback"]
            rows = _yfinance_rows(fallback["ticker"], config.partial_as_of)
            content = _canonical_rows(rows)
            url = fallback["source_url"]
            raw_format = "YFINANCE_ADJUSTED_CLOSE_EXPORT"
        retrieval_currencies[source_id] = source.get("programmatic_fallback", {}).get("listing_currency", source["source_currency"])
        record = {"source_id": source_id, "candidate_id": source["candidate_id"], "source_url": url, "retrieved_at": retrieved_at, "raw_format": raw_format, **_write_raw(raw_dir, source_id, content)}
        records.append(record)
        observations[source_id] = normalize_month_end_observations(rows, start=config.warmup_start, full_end=config.full_end, partial_as_of=config.partial_as_of, max_staleness_days=config.max_staleness_days)

    fx_raw = config_raw["fx_source"]
    fx_content = fetch_url(fx_raw["source_url"])
    fx_rows = _fred_rows(fx_content, "DEXUSEU")
    records.append({"source_id": fx_raw["source_id"], "source_url": fx_raw["source_url"], "retrieved_at": retrieved_at, "raw_format": "FRED_CSV", **_write_raw(raw_dir, fx_raw["source_id"], fx_content)})
    fx = {period: level for period, (_, level) in normalize_month_end_observations(fx_rows, start=config.warmup_start, full_end=config.full_end, partial_as_of=config.partial_as_of, max_staleness_days=config.max_staleness_days).items()}

    cash_rows: dict[str, tuple[tuple[date, Decimal], ...]] = {}
    for source in config_raw["cash_sources"]:
        content = fetch_url(source["source_url"])
        records.append({"source_id": source["source_id"], "source_url": source["source_url"], "retrieved_at": retrieved_at, "raw_format": "ECB_CSV", **_write_raw(raw_dir, source["source_id"], content)})
        cash_rows[source["source_id"]] = tuple(_ecb_rows(content))

    table_rows, benchmark_rows = [], []
    tracked = {source.source_id: source for source in config.sources if source.track in {"CONDITIONAL_INDEX_PROXY", "BENCHMARK_ONLY"}}
    records_by_source = {record["source_id"]: record for record in records}
    for source_id, monthly_observations in observations.items():
        source = tracked[source_id]
        monthly = {period: level for period, (_, level) in monthly_observations.items()}
        currency = retrieval_currencies[source_id]
        if currency == "EUR":
            eur = monthly
        elif currency == "USD":
            eur = convert_usd_monthly_levels_to_eur(monthly, fx)
        else:
            raise ValueError(f"{source_id} fallback currency must be EUR or USD")
        for period, level in eur.items():
            observed = monthly_observations[period][0]
            record = records_by_source[source_id]
            row = (period, source.candidate_id, source_id, observed, record["retrieved_at"], record["source_hash"], record["source_url"], level)
            (table_rows if source.track == "CONDITIONAL_INDEX_PROXY" else benchmark_rows).append(row)
    table_rows.sort()
    table = "month_end,candidate_id,source_id,observation_date,available_at,retrieved_at,source_hash,source_url,total_return_level_eur\n" + "".join(f"{period.isoformat()},{candidate},{source_id},{observed.isoformat()},{observed.isoformat()},{retrieved_at},{source_hash},{source_url},{level}\n" for period, candidate, source_id, observed, retrieved_at, source_hash, source_url, level in table_rows)
    (DATA / "monthly_total_return_eur.csv").write_text(table, encoding="utf-8", newline="")
    benchmark = "month_end,benchmark_id,source_id,observation_date,available_at,retrieved_at,source_hash,source_url,total_return_level_eur\n" + "".join(f"{period.isoformat()},{candidate},{source_id},{observed.isoformat()},{observed.isoformat()},{retrieved_at},{source_hash},{source_url},{level}\n" for period, candidate, source_id, observed, retrieved_at, source_hash, source_url, level in benchmark_rows)
    (DATA / "monthly_benchmark_total_return_eur.csv").write_text(benchmark, encoding="utf-8", newline="")

    cash_returns = monthly_euro_cash_returns(cash_rows["ECB_DEPOSIT_FACILITY"], cash_rows["ECB_ESTR"], start=config.warmup_start, full_end=config.full_end, partial_as_of=config.partial_as_of)
    cash_table = "month_end,source_id,observation_date,available_at,retrieved_at,source_hash,source_url,cash_return_eur\n" + "".join(f"{period.isoformat()},{source_id},{observed.isoformat()},{observed.isoformat()},{records_by_source[source_id]['retrieved_at']},{records_by_source[source_id]['source_hash']},{records_by_source[source_id]['source_url']},{cash_return}\n" for period, (source_id, observed, cash_return) in cash_returns.items())
    (DATA / "monthly_cash_return_eur.csv").write_text(cash_table, encoding="utf-8", newline="")

    source_ids = tuple(record["source_id"] for record in records)
    coverage = {source_id: [config.warmup_start.isoformat(), config.full_end.isoformat(), config.partial_as_of.isoformat()] for source_id in source_ids}
    manifest = {"schema_version": "DRO_V1_VINTAGE", "retrieved_at": retrieved_at, "warmup_start": config.warmup_start.isoformat(), "full_end": config.full_end.isoformat(), "partial_as_of": config.partial_as_of.isoformat(), "partial_label": config.partial_label, "strategy_performance_calculated": False, "sources": records, "coverage": coverage, "normalized_files": _normalized_manifest_records()}
    validate_vintage_manifest(manifest, expected_sources=source_ids, warmup_start=config.warmup_start, full_end=config.full_end, partial_as_of=config.partial_as_of, vintage_root=DATA)
    manifest_path = DATA / "manifest.json"
    manifest_path.write_bytes(canonical_json(manifest))
    return manifest_path


def freeze_dro_v1_manifest() -> Path:
    """Rebind the manifest to already frozen raw and normalized input files without a network refresh."""
    config = load_dro_config(CONFIG)
    previous = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    records = []
    for item in previous["sources"]:
        path = DATA / item["path"]
        content = path.read_bytes()
        records.append({**item, "byte_count": len(content), "source_hash": hashlib.sha256(content).hexdigest()})
    source_ids = tuple(record["source_id"] for record in records)
    manifest = {"schema_version": "DRO_V1_VINTAGE", "retrieved_at": previous["retrieved_at"], "warmup_start": config.warmup_start.isoformat(), "full_end": config.full_end.isoformat(), "partial_as_of": config.partial_as_of.isoformat(), "partial_label": config.partial_label, "strategy_performance_calculated": False, "sources": records, "coverage": {source_id: [config.warmup_start.isoformat(), config.full_end.isoformat(), config.partial_as_of.isoformat()] for source_id in source_ids}, "normalized_files": _normalized_manifest_records()}
    validate_vintage_manifest(manifest, expected_sources=source_ids, warmup_start=config.warmup_start, full_end=config.full_end, partial_as_of=config.partial_as_of, vintage_root=DATA)
    path = DATA / "manifest.json"
    path.write_bytes(canonical_json(manifest))
    return path


if __name__ == "__main__":
    print(refresh_dro_v1())
