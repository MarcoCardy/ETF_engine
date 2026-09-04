from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from perpetual_engine.io import canonical_json, sha256_file
from perpetual_engine.data_sources import SourceArtifact, fetch_url, freeze_bytes, parse_fred_csv
from perpetual_engine.point_in_time import ObservationRow, validate_rows


_TARGETS = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
REQUIRED_V1_COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")
_REGISTERED_PARSERS = frozenset({"ecb_dfr", "fred_dgs10", "fred_brent", "bis_gli", "fred_cpi"})
_REQUIRED_SOURCE_MAPPINGS = {
    "ECB_DFR": ("ecb_dfr", "known_future"),
    "US_TREASURY_10Y": ("fred_dgs10", "past_only"),
    "BRENT_RETURN": ("fred_brent", "past_only"),
    "BIS_USD_CREDIT_YOY": ("bis_gli", "past_only"),
    "US_CPI_YOY": ("fred_cpi", "past_only"),
}
_MODEL = ("amazon/chronos-2", "29ec3766d36d6f73f0696f85560a422f50e8498c", "cpu")
_AVAILABILITY = {
    "ECB_DFR": "effective_date",
    "US_TREASURY_10Y": "treasury_next_business_day",
    "BRENT_RETURN": "observation_plus_7_days",
    "BIS_USD_CREDIT_YOY": "quarter_end_plus_4_months",
    "US_CPI_YOY": "following_month_end",
}
_ECB_URL = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata"
_ECB_KEY = "D.U2.EUR.4F.KR.DFR.LEV"
_BIS_URL = "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip"
_BIS_MEMBER = "WS_GLI_csv_flat.csv"
_BIS_FIELDS = (
    "FREQ:Frequency",
    "CURR_DENOM:Currency of denomination",
    "BORROWERS_CTY:Borrowers' country",
    "BORROWERS_SECTOR:Borrowers' sector",
    "LENDERS_SECTOR:Lending sector",
    "L_POS_TYPE:Position type",
    "L_INSTR:Type of instruments",
    "UNIT_MEASURE:Unit of measure",
    "TIME_PERIOD:Time period or range",
    "OBS_VALUE:Observation Value",
    "TITLE:Title",
)


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    url: str
    parser: str
    role: str


@dataclass(frozen=True)
class ChronosConfig:
    path: Path
    project_root: Path
    model_id: str
    model_revision: str
    device: str
    target_csv: Path
    target_manifest: Path
    targets: tuple[str, ...]
    prediction_length: int
    quantiles: tuple[float, ...]
    scenario_basis_points: int
    data_root: Path
    sources: tuple[SourceSpec, ...]
    evaluation_origins: int
    reported_horizons: tuple[int, ...]
    bootstrap_block_months: int
    bootstrap_resamples: int
    bootstrap_seed: int
    bootstrap_confidence: float
    config_hash: str


@dataclass(frozen=True)
class MonthlyTable:
    months: tuple[date, ...]
    names: tuple[str, ...]
    values: np.ndarray


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty relative path")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{field} escapes project root")
    return resolved


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _next_month_end(value: date) -> date:
    return _month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def load_chronos_config(path: Path) -> ChronosConfig:
    path = path.resolve()
    project_root = path.parent.parent.resolve()
    if not path.is_relative_to(project_root):
        raise ValueError("configuration path escapes project root")
    try:
        data = _mapping(json.loads(path.read_text(encoding="utf-8")), "configuration")
        model = _mapping(data["model"], "model")
        target = _mapping(data["target"], "target")
        evaluation = _mapping(data["evaluation"], "evaluation")
        bootstrap = _mapping(data["bootstrap"], "bootstrap")
    except (KeyError, OSError, json.JSONDecodeError) as error:
        raise ValueError("Chronos configuration is malformed") from error
    if data.get("schema_version") != "CHRONOS_CONFIG_V1":
        raise ValueError("Chronos configuration schema is invalid")
    model_values = (model.get("id"), model.get("revision"), model.get("device"))
    if model_values != _MODEL:
        raise ValueError("Chronos model contract is invalid")
    targets = tuple(target.get("columns", ()))
    if targets != _TARGETS:
        raise ValueError("Chronos targets must be WORLD, MOMENTUM, QUALITY, TREND in order")
    quantiles = tuple(data.get("quantiles", ()))
    if data.get("prediction_length") != 12 or quantiles != (0.1, 0.5, 0.9):
        raise ValueError("Chronos prediction contract is invalid")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("Chronos sources must be an array")
    sources: list[SourceSpec] = []
    for raw in raw_sources:
        source = _mapping(raw, "source")
        source_id, url, parser, role = source.get("id"), source.get("url"), source.get("parser"), source.get("role")
        if (
            not all(isinstance(value, str) and value for value in (source_id, url, parser, role))
            or parser not in _REGISTERED_PARSERS
            or role not in {"known_future", "past_only"}
        ):
            raise ValueError("Chronos source is invalid or has an unregistered parser")
        sources.append(SourceSpec(source_id, url, parser, role))
    source_ids = tuple(source.source_id for source in sources)
    if source_ids != REQUIRED_V1_COVARIATES:
        raise ValueError("Chronos requires each v1 source exactly once")
    if any(
        (source.parser, source.role) != _REQUIRED_SOURCE_MAPPINGS[source.source_id]
        for source in sources
        if source.source_id in _REQUIRED_SOURCE_MAPPINGS
    ) or any(source.role == "known_future" and source.source_id != "ECB_DFR" for source in sources):
        raise ValueError("Chronos source mapping is invalid")
    try:
        reported_horizons = tuple(evaluation["reported_horizons"])
        numeric_values = (data["scenario_basis_points"], evaluation["origins"], bootstrap["block_months"], bootstrap["resamples"], bootstrap["seed"])
        confidence = bootstrap["confidence"]
    except KeyError as error:
        raise ValueError("Chronos configuration is incomplete") from error
    if numeric_values != (100, 36, 6, 2000, 42) or reported_horizons != (1, 3, 6, 12) or confidence != 0.95:
        raise ValueError("Chronos evaluation contract is invalid")
    return ChronosConfig(
        path, project_root, model_values[0], model_values[1], model_values[2],
        _path(project_root, target.get("csv"), "target.csv"), _path(project_root, target.get("manifest"), "target.manifest"),
        targets, 12, (0.1, 0.5, 0.9), 100, _path(project_root, data.get("data_root"), "data_root"), tuple(sources),
        36, reported_horizons, 6, 2000, 42, 0.95, hashlib.sha256(canonical_json(data)).hexdigest(),
    )


def load_target_snapshot(config: ChronosConfig) -> tuple[MonthlyTable, str, str]:
    try:
        target_bytes = config.target_csv.read_bytes()
        manifest_bytes = config.target_manifest.read_bytes()
        manifest = _mapping(json.loads(manifest_bytes.decode("utf-8")), "target manifest")
        expected_hash = _mapping(manifest["generated_sha256"], "target manifest generated_sha256")["monthly_returns.csv"]
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("target manifest is malformed") from error
    target_hash = hashlib.sha256(target_bytes).hexdigest()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest.get("schema_version") != "FOUR_SLEEVE_REPORT_V1" or not isinstance(expected_hash, str) or expected_hash != target_hash:
        raise ValueError("target hash does not match manifest")
    try:
        rows = list(csv.reader(io.StringIO(target_bytes.decode("utf-8"), newline="")))
    except UnicodeDecodeError as error:
        raise ValueError("target CSV is unreadable") from error
    required_header = ("month", *config.targets)
    if not rows or tuple(rows[0][:len(required_header)]) != required_header:
        raise ValueError("target CSV required columns are missing or reordered")
    months: list[date] = []
    values: list[list[float]] = []
    for row in rows[1:]:
        if len(row) < len(required_header):
            raise ValueError("target CSV row is incomplete")
        try:
            month = date.fromisoformat(row[0])
            row_values = [float(value) for value in row[1:len(required_header)]]
        except ValueError as error:
            raise ValueError("target CSV row is malformed") from error
        if month != _month_end(month):
            raise ValueError("target month must be month-end")
        if not all(math.isfinite(value) for value in row_values):
            raise ValueError("target values must be finite")
        if months and month != _next_month_end(months[-1]):
            raise ValueError("target months must be unique, ordered, and contiguous")
        months.append(month)
        values.append(row_values)
    if not months:
        raise ValueError("target CSV has no observations")
    array = np.asarray(values, dtype=float).T
    array.flags.writeable = False
    return MonthlyTable(tuple(months), config.targets, array), target_hash, manifest_hash


def load_target_table(config: ChronosConfig) -> MonthlyTable:
    return load_target_snapshot(config)[0]


def covariate_names(config: ChronosConfig) -> tuple[str, ...]:
    return tuple(source.source_id for source in config.sources)


def _require_artifact(artifact: SourceArtifact, expected_url: str) -> None:
    if not isinstance(artifact, SourceArtifact) or artifact.source_url != expected_url:
        raise ValueError(f"source URL must be exactly {expected_url}")
    if artifact.retrieved_at.tzinfo is None or artifact.retrieved_at.utcoffset() is None:
        raise ValueError("artifact retrieved_at must be timezone-aware")
    if not artifact.local_path.is_file() or sha256_file(artifact.local_path) != artifact.source_hash:
        raise ValueError("source artifact SHA-256 does not reconcile")


def parse_ecb_dfr(artifact: SourceArtifact) -> tuple[ObservationRow, ...]:
    _require_artifact(artifact, _ECB_URL)
    with artifact.local_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if any(fields.count(field) != 1 for field in ("KEY", "TIME_PERIOD", "OBS_VALUE")):
            raise ValueError("ECB CSV requires unique KEY, TIME_PERIOD, and OBS_VALUE columns")
        rows: list[ObservationRow] = []
        seen: set[date] = set()
        for record in reader:
            if (record.get("KEY") or "").strip() != _ECB_KEY:
                raise ValueError(f"ECB KEY must be exactly {_ECB_KEY}")
            try:
                observed = date.fromisoformat((record.get("TIME_PERIOD") or "").strip())
                value = Decimal((record.get("OBS_VALUE") or "").strip())
            except (InvalidOperation, ValueError) as error:
                raise ValueError("ECB row is malformed") from error
            if not value.is_finite():
                raise ValueError("ECB OBS_VALUE must be finite")
            if observed in seen:
                raise ValueError("ECB has duplicate observations")
            seen.add(observed)
            rows.append(ObservationRow(
                "ECB_DFR", observed, observed,
                datetime(observed.year, observed.month, observed.day, tzinfo=timezone.utc),
                value, "percent_per_annum", artifact.source_url, artifact.retrieved_at, artifact.source_hash,
                quality_flags=("ECB_OFFICIAL", "EFFECTIVE_DATE"),
            ))
    if not rows:
        raise ValueError("ECB has no observations")
    return validate_rows(rows)


def _quarter_end(period: str) -> date:
    if len(period) != 7 or period[4:6] != "-Q" or period[6] not in "1234":
        raise ValueError("BIS period must use YYYY-Qn")
    try:
        return _month_end(date(int(period[:4]), int(period[6]) * 3, 1))
    except ValueError as error:
        raise ValueError("BIS period must use YYYY-Qn") from error


def _bis_available_at(period_end: date) -> datetime:
    month_index = period_end.year * 12 + period_end.month - 1 + 4
    year, month_zero = divmod(month_index, 12)
    available = _month_end(date(year, month_zero + 1, 1))
    return datetime(available.year, available.month, available.day, 23, 59, 59, tzinfo=timezone.utc)


def parse_bis_gli(artifact: SourceArtifact) -> tuple[ObservationRow, ...]:
    _require_artifact(artifact, _BIS_URL)
    try:
        with zipfile.ZipFile(artifact.local_path) as archive:
            if archive.namelist() != [_BIS_MEMBER]:
                raise ValueError(f"BIS ZIP must contain only {_BIS_MEMBER}")
            with archive.open(_BIS_MEMBER) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = tuple(reader.fieldnames or ())
                if any(fields.count(field) != 1 for field in _BIS_FIELDS):
                    raise ValueError("BIS CSV required headers are missing or duplicated")
                rows: list[ObservationRow] = []
                dimensions = _BIS_FIELDS[:7]
                expected = ("Q", "USD", "3P", "N", "A", "I", "B")
                for record in reader:
                    if tuple((record.get(field) or "").strip() for field in dimensions) != expected or (record.get(_BIS_FIELDS[7]) or "").strip() != "771":
                        continue
                    period = (record.get(_BIS_FIELDS[8]) or "").strip()
                    period_end = _quarter_end(period)
                    try:
                        value = Decimal((record.get(_BIS_FIELDS[9]) or "").strip())
                    except InvalidOperation as error:
                        raise ValueError("BIS observation value is malformed") from error
                    if not value.is_finite():
                        raise ValueError("BIS observation value must be finite")
                    rows.append(ObservationRow(
                        "BIS_USD_CREDIT_YOY", period_end, period_end, _bis_available_at(period_end),
                        value, "percent_yoy", artifact.source_url, artifact.retrieved_at, artifact.source_hash,
                        quality_flags=("BIS_OFFICIAL", "QUARTER_END_PLUS_4_MONTHS"),
                    ))
    except zipfile.BadZipFile as error:
        raise ValueError("BIS ZIP is malformed") from error
    if not rows:
        raise ValueError("BIS has no observations")
    return validate_rows(rows)


def parse_fred_covariate(artifact: SourceArtifact, series_id: str) -> tuple[ObservationRow, ...]:
    if series_id == "DGS10":
        return parse_fred_csv(
            artifact, series_id=series_id, unit="percent_per_annum", source_scale="raw",
            frequency="daily", availability="treasury_next_business_day",
        )
    if series_id == "DCOILBRENTEU":
        rows = parse_fred_csv(
            artifact, series_id=series_id, unit="usd_per_barrel", source_scale="raw",
            frequency="daily", availability="observation_end",
        )
        return validate_rows(tuple(replace(row, available_at=row.available_at + timedelta(days=7)) for row in rows))
    if series_id == "CPIAUCNS":
        rows = parse_fred_csv(
            artifact, series_id=series_id, unit="index_1982_1984_100", source_scale="raw",
            frequency="monthly", availability="month_end",
        )
        return validate_rows(tuple(replace(
            row,
            available_at=datetime(
                _next_month_end(row.observation_date).year,
                _next_month_end(row.observation_date).month,
                _next_month_end(row.observation_date).day,
                23, 59, 59, tzinfo=timezone.utc,
            ),
        ) for row in rows))
    raise ValueError("FRED covariate series must be DGS10, DCOILBRENTEU, or CPIAUCNS")


def _eligible(rows: Iterable[ObservationRow], cutoff: datetime) -> tuple[ObservationRow, ...]:
    return tuple(row for row in validate_rows(rows) if row.available_at <= cutoff)


def _validate_covariate_rows(rows: Iterable[ObservationRow]) -> tuple[ObservationRow, ...]:
    validated = validate_rows(rows)
    dates = [(row.series_id, row.observation_date) for row in validated]
    if len(dates) != len(set(dates)):
        raise ValueError("covariate source has a duplicate observation date")
    return validated


def _daily_month_mean(rows: Iterable[ObservationRow], month: date, cutoff: datetime) -> float:
    values = [float(row.value) for row in _eligible(rows, cutoff) if _month_end(row.observation_date) == month]
    if not values:
        raise ValueError(f"daily series is unavailable for {month.isoformat()}")
    return math.fsum(values) / len(values)


def _previous_year(value: date) -> date:
    return _month_end(date(value.year - 1, value.month, 1))


def normalize_covariates(
    config: ChronosConfig,
    target_months: tuple[date, ...],
    rows_by_id: Mapping[str, tuple[ObservationRow, ...]],
) -> MonthlyTable:
    if not target_months or any(month != _month_end(month) for month in target_months) or any(
        current != _next_month_end(previous) for previous, current in zip(target_months, target_months[1:])
    ):
        raise ValueError("target months must be unique, ordered, contiguous month ends")
    names = covariate_names(config)
    if names != REQUIRED_V1_COVARIATES or set(rows_by_id) != set(names):
        raise ValueError("covariate sources must match the configured v1 contract")
    expected_series = {
        "ECB_DFR": "ECB_DFR",
        "US_TREASURY_10Y": "DGS10",
        "BRENT_RETURN": "DCOILBRENTEU",
        "BIS_USD_CREDIT_YOY": "BIS_USD_CREDIT_YOY",
        "US_CPI_YOY": "CPIAUCNS",
    }
    rows = {source_id: _validate_covariate_rows(source_rows) for source_id, source_rows in rows_by_id.items()}
    if any(any(row.series_id != expected_series[source_id] for row in source_rows) for source_id, source_rows in rows.items()):
        raise ValueError("covariate row series does not match its configured source")
    columns: list[list[float]] = [[] for _ in names]
    for month in target_months:
        cutoff = datetime(month.year, month.month, month.day, 23, 59, 59, tzinfo=timezone.utc)
        ecb = [row for row in _eligible(rows["ECB_DFR"], cutoff) if row.observation_date <= month]
        if not ecb:
            raise ValueError(f"ECB is unavailable for {month.isoformat()}")
        columns[0].append(float(max(ecb, key=lambda row: row.observation_date).value))
        columns[1].append(_daily_month_mean(rows["US_TREASURY_10Y"], month, cutoff))
        current_brent = _daily_month_mean(rows["BRENT_RETURN"], month, cutoff)
        previous_brent = _daily_month_mean(rows["BRENT_RETURN"], month.replace(day=1) - timedelta(days=1), cutoff)
        if current_brent <= 0 or previous_brent <= 0:
            raise ValueError("normalized covariate values must be finite")
        columns[2].append(math.log(current_brent / previous_brent))
        bis = [row for row in _eligible(rows["BIS_USD_CREDIT_YOY"], cutoff) if row.period_end <= month]
        if not bis:
            raise ValueError(f"BIS is unavailable for {month.isoformat()}")
        columns[3].append(float(max(bis, key=lambda row: row.period_end).value))
        cpi_month = month.replace(day=1) - timedelta(days=1)
        eligible_cpi = {row.observation_date: row for row in _eligible(rows["US_CPI_YOY"], cutoff)}
        current_cpi, prior_cpi = eligible_cpi.get(cpi_month), eligible_cpi.get(_previous_year(cpi_month))
        if current_cpi is None or prior_cpi is None:
            raise ValueError(f"CPI is unavailable for {month.isoformat()}")
        if prior_cpi.value <= 0:
            raise ValueError("normalized covariate values must be finite")
        columns[4].append(100.0 * (float(current_cpi.value / prior_cpi.value) - 1.0))
    array = np.asarray(columns, dtype=float)
    if array.shape != (len(names), len(target_months)) or not np.isfinite(array).all():
        raise ValueError("normalized covariate values must be finite")
    array.flags.writeable = False
    return MonthlyTable(target_months, names, array)


def _parse_source(spec: SourceSpec, artifact: SourceArtifact) -> tuple[ObservationRow, ...]:
    if spec.parser == "ecb_dfr":
        return parse_ecb_dfr(artifact)
    if spec.parser == "bis_gli":
        return parse_bis_gli(artifact)
    series_id = {"fred_dgs10": "DGS10", "fred_brent": "DCOILBRENTEU", "fred_cpi": "CPIAUCNS"}.get(spec.parser)
    if series_id is None:
        raise ValueError("Chronos source parser is not registered")
    return parse_fred_covariate(artifact, series_id)


def _covariate_bytes(table: MonthlyTable) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("month", *table.names))
    for index, month in enumerate(table.months):
        writer.writerow((month.isoformat(), *(format(float(value), ".17g") for value in table.values[:, index])))
    return output.getvalue().encode("utf-8")


def _vintage_identity(
    config_hash: str,
    sources: Iterable[Mapping[str, str]],
    raw_files_hashes: Mapping[str, str],
    covariates_hash: str,
) -> dict[str, Any]:
    return {
        "config_hash": config_hash,
        "sources": [
            {"id": source["id"], "url": source["url"], "sha256": source["sha256"]}
            for source in sources
        ],
        "raw_files_sha256": dict(sorted(raw_files_hashes.items())),
        "availability": _AVAILABILITY,
        "covariates_sha256": covariates_hash,
    }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _json_object(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is malformed") from error
    if not isinstance(value, dict):
        raise ValueError(f"{name} is malformed")
    return value


def _contained_file(root: Path, relative: Any, name: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{name} path is invalid")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"{name} path escapes its vintage or is missing")
    return path


def _raw_file_hashes(vintage: Path) -> dict[str, str]:
    raw_root = (vintage / "raw").resolve()
    if not raw_root.is_relative_to(vintage.resolve()) or not raw_root.is_dir():
        raise ValueError("raw files path escapes its vintage or is missing")
    hashes: dict[str, str] = {}
    for path in sorted(raw_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(raw_root):
            raise ValueError("raw files path escapes its vintage")
        hashes[path.relative_to(vintage).as_posix()] = sha256_file(resolved)
    if not hashes:
        raise ValueError("raw files are missing")
    return hashes


def _load_vintage(
    config: ChronosConfig,
    vintage: Path,
    vintage_id: str,
    *,
    staging_root: Path | None = None,
) -> MonthlyTable:
    if not _is_sha256(vintage_id):
        raise ValueError("vintage ID is invalid")
    vintages_root = (config.data_root / "vintages").resolve()
    vintage = vintage.resolve()
    allowed_root = vintages_root if staging_root is None else staging_root.resolve()
    if not allowed_root.is_relative_to(config.data_root.resolve()):
        raise ValueError("staging path escapes the configured data root")
    if not vintage.is_relative_to(allowed_root):
        raise ValueError("vintage path escapes the configured data root or staging directory")
    manifest_path = vintage / "manifest.json"
    manifest = _json_object(manifest_path, "vintage manifest")
    if set(manifest) != {"schema_version", "vintage_id", "config_hash", "sources", "raw_files_sha256", "availability", "generated_sha256"}:
        raise ValueError("vintage manifest schema is invalid")
    if manifest.get("schema_version") != "CHRONOS_VINTAGE_V1":
        raise ValueError("vintage manifest schema is invalid")
    if manifest.get("config_hash") != config.config_hash:
        raise ValueError("vintage config hash does not match")
    sources = manifest.get("sources")
    expected_sources = tuple((source.source_id, source.url, source.parser, source.role) for source in config.sources)
    if not isinstance(sources, list) or len(sources) != len(expected_sources):
        raise ValueError("vintage sources are invalid")
    raw_paths: set[Path] = set()
    for record, expected in zip(sources, expected_sources):
        if not isinstance(record, dict) or set(record) != {"id", "url", "parser", "role", "sha256", "raw_path"}:
            raise ValueError("vintage source record is invalid")
        if tuple(record.get(field) for field in ("id", "url", "parser", "role")) != expected or not _is_sha256(record.get("sha256")):
            raise ValueError("vintage source identity is invalid")
        raw_path = _contained_file(vintage, record["raw_path"], "raw source")
        if raw_path in raw_paths or sha256_file(raw_path) != record["sha256"]:
            raise ValueError("raw hash does not match")
        raw_paths.add(raw_path)
    recorded_raw_files = manifest.get("raw_files_sha256")
    if not isinstance(recorded_raw_files, dict) or any(
        not isinstance(path, str) or not _is_sha256(file_hash)
        for path, file_hash in recorded_raw_files.items()
    ) or recorded_raw_files != _raw_file_hashes(vintage):
        raise ValueError("raw files or raw metadata hash does not match")
    if manifest.get("availability") != _AVAILABILITY:
        raise ValueError("vintage availability identity is invalid")
    generated = manifest.get("generated_sha256")
    if not isinstance(generated, dict) or set(generated) != {"covariates.csv"} or not _is_sha256(generated["covariates.csv"]):
        raise ValueError("vintage generated hashes are invalid")
    covariate_path = _contained_file(vintage, "covariates.csv", "normalized covariates")
    if sha256_file(covariate_path) != generated["covariates.csv"]:
        raise ValueError("normalized hash does not match")
    identity = _vintage_identity(config.config_hash, sources, recorded_raw_files, generated["covariates.csv"])
    computed_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    if manifest.get("vintage_id") != vintage_id or computed_id != vintage_id:
        raise ValueError("vintage ID does not match its immutable identity")
    try:
        with covariate_path.open("r", encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.reader(handle))
    except (OSError, UnicodeError) as error:
        raise ValueError("normalized covariates are unreadable") from error
    header = ("month", *covariate_names(config))
    if not csv_rows or tuple(csv_rows[0]) != header:
        raise ValueError("normalized covariate header is invalid")
    months: list[date] = []
    values: list[list[float]] = []
    for row in csv_rows[1:]:
        if len(row) != len(header):
            raise ValueError("normalized covariate row is incomplete")
        try:
            month = date.fromisoformat(row[0])
            row_values = [float(value) for value in row[1:]]
        except ValueError as error:
            raise ValueError("normalized covariate row is malformed") from error
        if month != _month_end(month) or (months and month != _next_month_end(months[-1])):
            raise ValueError("normalized months must be unique, ordered, and contiguous")
        if not all(math.isfinite(value) for value in row_values):
            raise ValueError("normalized covariate values must be finite")
        months.append(month)
        values.append(row_values)
    if not months:
        raise ValueError("normalized covariates have no observations")
    array = np.asarray(values, dtype=float).T
    array.flags.writeable = False
    return MonthlyTable(tuple(months), covariate_names(config), array)


def _replace_current_pointer(data_root: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=data_root, prefix=".current-", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(data_root / "current_manifest.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def refresh_chronos_data(
    config_path: Path,
    *,
    fetcher: Callable[[str], bytes] = fetch_url,
    retrieved_at: datetime | None = None,
) -> str:
    config = load_chronos_config(config_path)
    targets = load_target_table(config)
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if not config.data_root.resolve().is_relative_to(config.project_root):
        raise ValueError("data root escapes project root")
    config.data_root.mkdir(parents=True, exist_ok=True)
    data_root = config.data_root.resolve()
    staging_path = config.data_root / ".staging"
    staging_path.mkdir(exist_ok=True)
    staging_root = staging_path.resolve()
    if not staging_root.is_relative_to(data_root):
        raise ValueError("staging path escapes the configured data root")
    stage = Path(tempfile.mkdtemp(prefix="refresh-", dir=staging_root)).resolve()
    if not stage.is_relative_to(staging_root):
        raise ValueError("refresh staging directory escapes the configured data root")
    published: Path | None = None
    try:
        source_rows: dict[str, tuple[ObservationRow, ...]] = {}
        records: list[dict[str, str]] = []
        for spec in config.sources:
            content = fetcher(spec.url)
            if not isinstance(content, bytes):
                raise ValueError("source fetcher must return bytes")
            artifact = freeze_bytes(content, spec.url, retrieved_at, stage / "raw", f"chronos-v1:{spec.parser}")
            source_rows[spec.source_id] = _parse_source(spec, artifact)
            records.append({
                "id": spec.source_id,
                "url": spec.url,
                "parser": spec.parser,
                "role": spec.role,
                "sha256": artifact.source_hash,
                "raw_path": artifact.local_path.relative_to(stage).as_posix(),
            })
        table = normalize_covariates(config, targets.months, source_rows)
        normalized = _covariate_bytes(table)
        normalized_hash = hashlib.sha256(normalized).hexdigest()
        raw_files_hashes = _raw_file_hashes(stage)
        identity = _vintage_identity(config.config_hash, records, raw_files_hashes, normalized_hash)
        vintage_id = hashlib.sha256(canonical_json(identity)).hexdigest()
        manifest = {
            "schema_version": "CHRONOS_VINTAGE_V1",
            "vintage_id": vintage_id,
            "config_hash": config.config_hash,
            "sources": records,
            "raw_files_sha256": raw_files_hashes,
            "availability": _AVAILABILITY,
            "generated_sha256": {"covariates.csv": normalized_hash},
        }
        manifest_bytes = canonical_json(manifest)
        (stage / "covariates.csv").write_bytes(normalized)
        (stage / "manifest.json").write_bytes(manifest_bytes)
        _load_vintage(config, stage, vintage_id, staging_root=staging_root)
        vintages_path = config.data_root / "vintages"
        vintages_path.mkdir(exist_ok=True)
        vintages = vintages_path.resolve()
        if not vintages.is_relative_to(data_root):
            raise ValueError("vintages path escapes the configured data root")
        destination = vintages / vintage_id
        if destination.exists():
            _load_vintage(config, destination, vintage_id)
            if (destination / "manifest.json").read_bytes() != manifest_bytes:
                raise ValueError("existing vintage is not byte-identical")
        else:
            stage.replace(destination)
            published = destination
        pointer = canonical_json({
            "schema_version": "CHRONOS_CURRENT_V1",
            "vintage_id": vintage_id,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        })
        try:
            _replace_current_pointer(config.data_root, pointer)
        except OSError as error:
            if published is not None and published.exists():
                shutil.rmtree(published)
            raise ValueError("current vintage pointer could not be published") from error
        return vintage_id
    finally:
        if stage.exists() and stage.is_relative_to(staging_root.resolve()):
            shutil.rmtree(stage)


def load_covariate_table(config: ChronosConfig) -> tuple[MonthlyTable, str]:
    data_root = config.data_root.resolve()
    if not data_root.is_relative_to(config.project_root.resolve()):
        raise ValueError("data root escapes project root")
    pointer_path = data_root / "current_manifest.json"
    pointer = _json_object(pointer_path, "current vintage pointer")
    if set(pointer) != {"schema_version", "vintage_id", "manifest_sha256"} or pointer.get("schema_version") != "CHRONOS_CURRENT_V1":
        raise ValueError("current vintage pointer schema is invalid")
    vintage_id = pointer.get("vintage_id")
    if not _is_sha256(vintage_id):
        raise ValueError("vintage ID is invalid")
    vintages_root = (data_root / "vintages").resolve()
    if not vintages_root.is_relative_to(data_root):
        raise ValueError("vintages path escapes the configured data root")
    vintage = (vintages_root / vintage_id).resolve()
    if not vintage.is_relative_to(vintages_root):
        raise ValueError("vintage path escapes the configured data root")
    manifest_path = vintage / "manifest.json"
    expected_manifest_hash = pointer.get("manifest_sha256")
    if not _is_sha256(expected_manifest_hash) or not manifest_path.is_file() or sha256_file(manifest_path) != expected_manifest_hash:
        raise ValueError("vintage manifest hash does not match pointer")
    return _load_vintage(config, vintage, vintage_id), vintage_id
