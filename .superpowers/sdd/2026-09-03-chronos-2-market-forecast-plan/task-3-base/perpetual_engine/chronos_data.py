from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from perpetual_engine.io import canonical_json, sha256_file
from perpetual_engine.data_sources import SourceArtifact, parse_fred_csv
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


def load_target_table(config: ChronosConfig) -> MonthlyTable:
    try:
        manifest = _mapping(json.loads(config.target_manifest.read_text(encoding="utf-8")), "target manifest")
        expected_hash = _mapping(manifest["generated_sha256"], "target manifest generated_sha256")["monthly_returns.csv"]
    except (KeyError, OSError, json.JSONDecodeError) as error:
        raise ValueError("target manifest is malformed") from error
    if manifest.get("schema_version") != "FOUR_SLEEVE_REPORT_V1" or not isinstance(expected_hash, str) or expected_hash != sha256_file(config.target_csv):
        raise ValueError("target hash does not match manifest")
    try:
        with config.target_csv.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except OSError as error:
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
    return MonthlyTable(tuple(months), config.targets, array)


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
